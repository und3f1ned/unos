"""What a registry publishes of a pinned image for one platform (ADR-0408).

An app's update reads, before it pulls anything, the image that the catalog pins for each of its
containers as the registry serves it for the host's platform: the index of the pinned digest, the
image manifest of the platform (its layers, each with its compressed size) and the image's
configuration (when it was built, ``created``, and the digests of its unpacked layers,
``rootfs.diff_ids``). From these the update tells whether the catalog's image is newer than the one
that runs, which of its layers Docker lacks and so what the pull writes where Docker keeps its images
(ADR-0493).

The reads are anonymous and read-only, over HTTPS only, through the registry's token handshake for
public images (Docker Hub, GHCR, where the catalog names LinuxServer's images, ADR-0426), as
``scripts/catalog-platforms.py`` reads the manifest lists (ADR-0403). Every answer read by its
digest is checked against that digest, a blob that the registry redirects to a CDN is fetched there
without the registry's token, and an answer larger than MAX_BYTES is refused. Nothing is pulled and
nothing is written. A registry that cannot be read is a RegistryError, never a guess.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, UTC

import requests

from . import platforms

TIMEOUT_SECONDS = 30
# The most that one answer (a manifest, a configuration, a token) may have.
MAX_BYTES = 4 * 1024 * 1024
MANIFEST_TYPES = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])
_REGISTRY_ALIASES = {"docker.io": "registry-1.docker.io", "index.docker.io": "registry-1.docker.io"}
_PINNED = re.compile(r"(?P<name>[^@\s]+):(?P<tag>[^@:/\s]+)@(?P<digest>sha256:[0-9a-f]{64})")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")

# A transport answers one GET: (url, headers) -> (status, headers with lower-case names, body). It
# follows no redirect.
Transport = Callable[[str, dict[str, str]], tuple[int, dict[str, str], bytes]]


class RegistryError(Exception):
    """The registry's answer for an image could not be read, or was not what was asked for."""


class NotPublished(RegistryError):
    """The image's index has no manifest for the platform."""


@dataclass(frozen=True)
class RemoteImage:
    """A pinned image as its registry serves it for one platform."""

    ref: str
    platform: str
    # The digest of its configuration: Docker's id of the image once pulled.
    config_digest: str
    # When it was built (its configuration's ``created``); None when the configuration has none.
    created: datetime | None
    # Its layers in order: the digest and the size of each compressed blob, and the digests of
    # the unpacked layers (diff ids), one for each.
    layers: tuple[tuple[str, int], ...]
    diff_ids: tuple[str, ...]

    @property
    def download_bytes(self) -> int:
        return sum(size for _, size in self.layers)


def _requests_transport(url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
    if not url.startswith("https://"):
        raise RegistryError(f"{url}: not HTTPS")
    try:
        with requests.get(
            url, headers={"User-Agent": "unos", **headers}, timeout=TIMEOUT_SECONDS, allow_redirects=False,
            stream=True,
        ) as response:
            body = b""
            for chunk in response.iter_content(64 * 1024):
                body += chunk
                if len(body) > MAX_BYTES:
                    raise RegistryError(f"{url}: an answer larger than {MAX_BYTES} bytes")
            return response.status_code, {k.lower(): v for k, v in response.headers.items()}, body
    except requests.RequestException as exc:
        raise RegistryError(f"{url}: {exc}") from exc


# The transport that the reads use; tests replace it.
transport: Transport = _requests_transport


def parse_reference(ref: str) -> tuple[str, str, str]:
    """(registry host, repository, digest) of a pinned reference ``name:tag@sha256:...``;
    RegistryError for one that is not pinned."""
    match = _PINNED.fullmatch(ref)
    if match is None:
        raise RegistryError(f"{ref} is not pinned by a digest")
    parts = match["name"].split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        host, repo = parts[0], "/".join(parts[1:])
    else:
        host, repo = "docker.io", match["name"]
    if host == "docker.io" and "/" not in repo:
        repo = f"library/{repo}"
    return _REGISTRY_ALIASES.get(host, host), repo, match["digest"]


def _platform_of(entry: dict) -> str | None:
    """"linux/arm64", "linux/arm/v7" of an index entry's or a configuration's platform."""
    os_name, arch, variant = entry.get("os"), entry.get("architecture"), entry.get("variant")
    if os_name != "linux" or not arch or arch == "unknown":
        return None
    return "/".join(part for part in ("linux", arch, variant if arch == "arm" else None) if part)


def _parse_time(value: object) -> datetime | None:
    """An RFC 3339 time of a configuration (nanoseconds cut to microseconds); None for none."""
    if not isinstance(value, str) or not value:
        return None
    text = re.sub(r"(\.\d{6})\d+", r"\1", value.strip()).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class _Reader:
    """Anonymous reads of one repository."""

    def __init__(self, host: str, repo: str) -> None:
        self.host, self.repo = host, repo
        self.token: str | None = None

    def _get(self, path: str, accept: str) -> tuple[int, dict[str, str], bytes]:
        url = f"https://{self.host}/v2/{self.repo}/{path}"
        headers = {"Accept": accept}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        status, answer, body = transport(url, headers)
        if status == 401 and self.token is None:
            self.token = self._anonymous_token(answer.get("www-authenticate", ""), url)
            return self._get(path, accept)
        if 300 <= status < 400:
            location = answer.get("location", "")
            if not location.startswith("https://"):
                raise RegistryError(f"{url}: a redirect to {location or 'nowhere'}")
            # A blob on a CDN: fetched there without the registry's token.
            status, answer, body = transport(location, {})
        if status != 200:
            raise RegistryError(f"{url}: HTTP {status}")
        return status, answer, body

    def _anonymous_token(self, challenge: str, url: str) -> str:
        fields = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        if not challenge.lower().startswith("bearer") or not fields.get("realm", "").startswith("https://"):
            raise RegistryError(f"{url}: the registry asks for credentials")
        query = {"scope": f"repository:{self.repo}:pull"}
        if "service" in fields:
            query["service"] = fields["service"]
        status, _, body = transport(f"{fields['realm']}?{urllib.parse.urlencode(query)}", {})
        if status != 200:
            raise RegistryError(f"{url}: no anonymous token (HTTP {status})")
        try:
            answer = json.loads(body)
            token = answer.get("token") or answer.get("access_token")
        except (ValueError, AttributeError):
            token = None
        if not isinstance(token, str) or not token:
            raise RegistryError(f"{url}: the token answer has no token")
        return token

    def by_digest(self, kind: str, digest: str, accept: str) -> tuple[dict, str]:
        """The JSON of the manifest or blob ``digest``, checked against it, and its media type."""
        if not _DIGEST.fullmatch(digest):
            raise RegistryError(f"{digest} is no sha256 digest")
        _, answer, body = self._get(f"{kind}/{digest}", accept)
        if "sha256:" + hashlib.sha256(body).hexdigest() != digest:
            raise RegistryError(f"https://{self.host}/v2/{self.repo}/{kind}/{digest}: an answer of another digest")
        try:
            document = json.loads(body)
        except ValueError as exc:
            raise RegistryError(f"https://{self.host}/v2/{self.repo}/{kind}/{digest}: not JSON") from exc
        if not isinstance(document, dict):
            raise RegistryError(f"https://{self.host}/v2/{self.repo}/{kind}/{digest}: not a JSON object")
        media = answer.get("content-type", "").split(";")[0].strip() or str(document.get("mediaType", ""))
        return document, media


def read(ref: str, platform: str) -> RemoteImage:
    """The image that the pinned ``ref`` names for ``platform`` ("linux/arm64"), as its registry
    serves it. NotPublished when its index has no manifest for the platform; RegistryError when the
    registry cannot be read or answers something else."""
    host, repo, digest = parse_reference(ref)
    reader = _Reader(host, repo)
    try:
        manifest, _ = reader.by_digest("manifests", digest, MANIFEST_TYPES)
        if "manifests" in manifest:
            entries = [entry for entry in manifest.get("manifests") or [] if isinstance(entry, dict)]
            found = {_platform_of(entry.get("platform") or {}): entry for entry in entries}
            compatible = [p for p in sorted(p for p in found if p) if platforms.runs_on([p], platform)]
            chosen = platform if platform in found else (compatible[0] if compatible else None)
            if chosen is None:
                raise NotPublished(f"{ref} publishes no image for {platform}")
            manifest, _ = reader.by_digest("manifests", str(found[chosen].get("digest", "")), MANIFEST_TYPES)
        config_digest = str((manifest.get("config") or {}).get("digest", ""))
        config, _ = reader.by_digest("blobs", config_digest, "application/json")
        built_for = _platform_of(config)
        if built_for is not None and not platforms.runs_on([built_for], platform):
            raise NotPublished(f"{ref} is an image for {built_for}, not {platform}")
        layers = tuple((str(layer["digest"]), int(layer["size"])) for layer in manifest.get("layers") or [])
        diff_ids = tuple(str(d) for d in ((config.get("rootfs") or {}).get("diff_ids") or []))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise RegistryError(f"{ref}: an answer that is no image manifest ({exc!r})") from exc
    if len(layers) != len(diff_ids):
        raise RegistryError(f"{ref}: its manifest names {len(layers)} layers and its configuration {len(diff_ids)}")
    return RemoteImage(
        ref=ref, platform=platform, config_digest=config_digest, created=_parse_time(config.get("created")),
        layers=layers, diff_ids=diff_ids,
    )


def chain_ids(diff_ids: tuple[str, ...] | list[str]) -> list[str]:
    """The chain id of each layer of an image: the digest of the layer with all those below it, by
    which Docker's store (the graph driver's layers, and containerd's snapshots) keeps an unpacked
    layer, so that a layer is there only when the same layers are below it."""
    chains: list[str] = []
    for diff_id in diff_ids:
        chains.append(diff_id if not chains else "sha256:" + hashlib.sha256(f"{chains[-1]} {diff_id}".encode()).hexdigest())
    return chains


def parse_time(value: object) -> datetime | None:
    """Docker's or a registry's RFC 3339 time (``Created``); None when it has none."""
    return _parse_time(value)
