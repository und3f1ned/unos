#!/usr/bin/env python3
"""The images of the app catalog against their registries (ADR-0403, ADR-0410): the audit's tool.

    scripts/catalog-platforms.py check     exit 0 when the platforms recorded in
                                           supervisor/src/apps/image_platforms.json are those
                                           that the registries publish now
    scripts/catalog-platforms.py record    write the file from the registries
    scripts/catalog-platforms.py tags      exit 0 when the tag of every pinned image of the
                                           catalog still names the index it is pinned to

Run it with the supervisor's Python (it reads the catalog: `make catalog-platforms` and
`make catalog-tags`, or `supervisor/.venv/bin/python scripts/catalog-platforms.py check` by hand);
the weekly workflow (.github/workflows/audit.yml, ADR-0218) runs the same commands. It needs the
network and only reads: for every image of the catalog (the main container's, the companions' and
those of the hardware profiles) it asks the image's registry, anonymously through curl, for the
manifest of the pinned digest, and takes the platforms from the manifest list (the OCI index or
Docker's manifest list), or from the image's config when the reference is a single manifest; for
`tags` it asks with HEAD which digest the pinned tag names now. It pulls no layer and writes
nothing but the recorded file, and only for `record`.

Never a guess: an image that cannot be queried (a registry that refuses, a manifest it does not
know) is an error, and `record` then writes nothing. A manifest list that names no Linux platform
is recorded as none: the image publishes nothing a host can pull. Entries
that describe no platform ("unknown/unknown": the attestations that BuildKit adds) and those of
other systems than Linux are left out. Docker Hub counts every manifest it serves with GET against
its anonymous pull limit (its ratelimit-limit header named 100 an hour per address on 2026-11-18):
`check` and `record` ask for each image once; the HEAD requests of `tags` are not counted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
import subprocess
import tempfile
import time
import urllib.parse
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECORDED = ROOT / "supervisor" / "src" / "apps" / "image_platforms.json"

# Every media type a registry may answer a manifest request with.
MANIFEST_TYPES = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])
INDEX_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
TIMEOUT = 30
# A reference pinned by tag and digest, as the catalog writes it and the install reads it (ADR-0381):
# a tag, which holds no slash, then the index digest.
PINNED = re.compile(r"[^@\s]+:[^@:/\s]+@sha256:[0-9a-f]{64}")
# lscr.io is LinuxServer.io's redirect to its images on GHCR.
REGISTRY_ALIASES = {"lscr.io": "ghcr.io", "docker.io": "registry-1.docker.io", "index.docker.io": "registry-1.docker.io"}

# A transport answers one request: (method, url, headers) -> (status, headers, body). The
# headers' names are lower case. It follows no redirect: the caller does, without credentials.
Transport = Callable[[str, str, dict[str, str]], tuple[int, dict[str, str], bytes]]


class QueryError(Exception):
    """An image whose platforms could not be read."""


def curl_transport(method: str, url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
    """The real transport: the system's curl (on every workstation and CI runner, with the proxy and
    certificate settings of its environment), one retry after a server error, no redirect."""
    # A HEAD is curl's --head: with -X HEAD it would wait for the body that the length announces.
    verb = ["--head"] if method == "HEAD" else ["-X", method]
    command = ["curl", "-sS", "-i", "--proto", "=https", "--proto-redir", "=https", "--max-time", str(TIMEOUT), *verb, "-A", "unos-catalog-platforms"]
    for name, value in {"Accept-Encoding": "identity", **headers}.items():
        command += ["-H", f"{name}: {value}"]
    for attempt in range(2):
        try:
            done = subprocess.run([*command, url], capture_output=True, timeout=TIMEOUT + 10, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise QueryError(f"{url}: {exc}") from exc
        if done.returncode == 0:
            status, answer, body = parse_response(done.stdout)
            if status < 500 or attempt == 1:
                return status, answer, body
        elif attempt == 1:
            raise QueryError(f"{url}: curl exit {done.returncode}: {done.stderr.decode(errors='replace').strip()}")
        time.sleep(2)
    raise QueryError(f"{url}: no answer")  # pragma: no cover


def parse_response(raw: bytes) -> tuple[int, dict[str, str], bytes]:
    """The status, the headers (names in lower case) and the body of curl's ``-i`` output; the
    blocks of any interim answer (100 Continue) are skipped."""
    while True:
        head, sep, body = raw.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split()
        if not sep or len(parts) < 2 or not parts[1].isdigit():
            raise QueryError(f"not an HTTP answer: {lines[0][:80]!r}")
        status = int(parts[1])
        if status == 100:
            raw = body
            continue
        answer = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            answer[name.strip().lower()] = value.strip()
        return status, answer, body


def parse_reference(ref: str) -> tuple[str, str, str]:
    """(registry host, repository, tag or digest) of an image reference of the catalog: the
    digest of ``name:tag@sha256:...`` wins, a name without a tag is refused."""
    name, _, digest = ref.partition("@")
    tag = "latest"
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        name, tag = name.rsplit(":", 1)
    parts = name.split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        registry, repo = parts[0], "/".join(parts[1:])
    else:
        registry, repo = "docker.io", name
    if registry == "docker.io" and "/" not in repo:
        repo = f"library/{repo}"
    if registry == "lscr.io":
        repo = repo if repo.startswith("linuxserver/") else f"linuxserver/{repo}"
    return REGISTRY_ALIASES.get(registry, registry), repo, digest or tag


def normalize(platform: dict) -> str | None:
    """"linux/arm64", "linux/arm/v7" for a platform entry; None for one to leave out."""
    os_name, arch, variant = platform.get("os"), platform.get("architecture"), platform.get("variant")
    if os_name != "linux" or not arch or arch == "unknown":
        return None
    # Only 32-bit arm is told by its variant; arm64's v8 and amd64's levels are no platform of their own.
    if arch != "arm":
        variant = None
    return "/".join(part for part in ("linux", arch, variant) if part)


class Registry:
    """Anonymous, read-only access to one registry's manifests."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self._tokens: dict[tuple[str, str], str] = {}

    def _get(self, host: str, repo: str, path: str, accept: str, method: str = "GET") -> tuple[int, dict[str, str], bytes]:
        url = f"https://{host}/v2/{repo}/{path}"
        headers = {"Accept": accept}
        token = self._tokens.get((host, repo))
        if token:
            headers["Authorization"] = f"Bearer {token}"
        status, answer, body = self.transport(method, url, headers)
        if status == 401 and not token:
            self._tokens[(host, repo)] = self._token(answer.get("www-authenticate", ""), repo, url)
            return self._get(host, repo, path, accept, method)
        return status, answer, body

    def _token(self, challenge: str, repo: str, url: str) -> str:
        fields = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        if not challenge.lower().startswith("bearer") or not fields.get("realm", "").startswith("https://"):
            raise QueryError(f"{url}: the registry asks for credentials ({challenge or 'no challenge'})")
        query = {"scope": f"repository:{repo}:pull"}
        if "service" in fields:
            query["service"] = fields["service"]
        status, _, body = self.transport("GET", f"{fields['realm']}?{urllib.parse.urlencode(query)}", {})
        if status != 200:
            raise QueryError(f"{url}: no anonymous token (HTTP {status})")
        try:
            token = json.loads(body).get("token") or json.loads(body).get("access_token")
        except ValueError:
            token = None
        if not token:
            raise QueryError(f"{url}: the token answer has no token")
        return token

    def _json(self, host: str, repo: str, path: str, accept: str) -> tuple[dict, dict[str, str]]:
        status, answer, body = self._get(host, repo, path, accept)
        if 300 <= status < 400 and answer.get("location", "").startswith("https://"):
            # A blob lives on a CDN: follow the redirect without the registry's token.
            status, answer, body = self.transport("GET", answer["location"], {})
        if status != 200:
            raise QueryError(f"https://{host}/v2/{repo}/{path}: HTTP {status}")
        try:
            return json.loads(body), answer
        except ValueError as exc:
            raise QueryError(f"https://{host}/v2/{repo}/{path}: not JSON") from exc

    def platforms(self, ref: str) -> list[str]:
        """The Linux platforms of ``ref``, sorted; QueryError when they cannot be read."""
        host, repo, reference = parse_reference(ref)
        try:
            manifest, answer = self._json(host, repo, f"manifests/{reference}", MANIFEST_TYPES)
            kind = answer.get("content-type", "").split(";")[0].strip() or manifest.get("mediaType", "")
            if kind in INDEX_TYPES or "manifests" in manifest:
                found = {normalize(entry.get("platform") or {}) for entry in manifest.get("manifests", [])}
            else:
                config, _ = self._json(host, repo, f"blobs/{manifest['config']['digest']}", "application/json")
                found = {normalize(config)}
        except (KeyError, TypeError, AttributeError) as exc:
            raise QueryError(f"{ref}: an answer that is no manifest ({exc!r})") from exc
        except QueryError as exc:
            raise QueryError(f"{ref}: {exc}") from exc
        found.discard(None)
        # An answer that lists no Linux platform (an empty index) is read, not a failure: the image
        # publishes none, and no host can pull it.
        return sorted(found)


    def digest(self, ref: str) -> str:
        """The digest of the manifest that the tag of ``ref`` names now (its index, for a
        multi-platform image), from a HEAD; from the body of a GET where the registry's answer
        names none. QueryError when it cannot be read."""
        host, repo, tag = parse_reference(ref.partition("@")[0])
        url = f"https://{host}/v2/{repo}/manifests/{tag}"
        status, answer, _ = self._get(host, repo, f"manifests/{tag}", MANIFEST_TYPES, "HEAD")
        if status != 200:
            raise QueryError(f"{ref}: {url}: HTTP {status}")
        named = answer.get("docker-content-digest", "")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", named):
            return named
        status, _, body = self._get(host, repo, f"manifests/{tag}", MANIFEST_TYPES)
        if status != 200:
            raise QueryError(f"{ref}: {url}: HTTP {status}")
        return "sha256:" + hashlib.sha256(body).hexdigest()

    def served(self, ref: str) -> bool:
        """Whether the registry still serves the manifest of the digest that ``ref`` pins (a HEAD):
        False only for a 404; QueryError for any other answer than 200, which says nothing of it."""
        host, repo, digest = parse_reference(ref)
        url = f"https://{host}/v2/{repo}/manifests/{digest}"
        status, _, _ = self._get(host, repo, f"manifests/{digest}", MANIFEST_TYPES, "HEAD")
        if status == 404:
            return False
        if status != 200:
            raise QueryError(f"{ref}: {url}: HTTP {status}")
        return True


def catalog_users() -> dict[str, list[str]]:
    """Every image reference of the catalog, once, in catalog order, with the ids of the templates
    whose containers run it (their own image or a hardware profile's)."""
    sys.path.insert(0, str(ROOT / "supervisor"))
    # Importing the catalog loads the settings: a database in memory and a secret of this run
    # alone keep it from creating files, and the data directory, which must exist (ADR-0419), is
    # the temporary one, where nothing is written.
    os.environ.setdefault("UNOS_DB_PATH", ":memory:")
    os.environ.setdefault("UNOS_JWT_SECRET", secrets.token_hex(32))
    os.environ.setdefault("UNOS_DATA_DIR", tempfile.gettempdir())
    from src.apps.catalog import APP_CATALOG

    users: dict[str, list[str]] = {}
    for template in APP_CATALOG:
        for spec in (template, *template.companions):
            for ref in (spec.image, *(profile.image for profile in spec.hardware if profile.image)):
                ids = users.setdefault(ref, [])
                if template.id not in ids:
                    ids.append(template.id)
    return users


def moved_tags(users: dict[str, list[str]], transport: Transport) -> tuple[list[str], list[str]]:
    """The pinned images whose tag names another index than the pinned one now, each line with the
    templates that run it, and the errors: an image that is not pinned by digest, or a tag that
    cannot be read."""
    registry = Registry(transport)
    lines: list[str] = []
    errors: list[str] = []
    for ref, templates in users.items():
        tagged, _, pinned = ref.partition("@")
        if not PINNED.fullmatch(ref):
            errors.append(f"{ref}: not pinned by tag and digest (name:tag@sha256:...)")
            continue
        try:
            now = registry.digest(ref)
            if now == pinned:
                continue
            gone = not registry.served(ref)
        except QueryError as exc:
            errors.append(str(exc))
            continue
        line = f"{', '.join(templates)}: {tagged} names {now}, not the pinned {pinned}"
        if gone:
            line += "; the pinned index is gone from the registry, so an install of it fails"
        lines.append(line)
    return lines, errors


def catalog_images() -> list[str]:
    """Every image reference of the catalog, once, in catalog order: each template's own, its
    companions' and those that a hardware profile substitutes."""
    return list(catalog_users())


def query_all(refs: list[str], transport: Transport) -> tuple[dict[str, list[str]], list[str]]:
    """The platforms of every reference, and the errors of those that could not be read."""
    registry = Registry(transport)
    found: dict[str, list[str]] = {}
    errors: list[str] = []
    for ref in refs:
        try:
            found[ref] = registry.platforms(ref)
        except QueryError as exc:
            errors.append(str(exc))
    return found, errors


def load_recorded(path: Path = RECORDED) -> dict[str, list[str]]:
    return json.loads(path.read_text())["images"]


def render(images: dict[str, list[str]]) -> str:
    return json.dumps({"images": dict(sorted(images.items()))}, indent=2) + "\n"


def differences(recorded: dict[str, list[str]], now: dict[str, list[str]], refs: list[str]) -> list[str]:
    """What differs between the recorded platforms and the registries', for the images of the
    catalog (``refs``), and between the catalog and the recorded file."""
    lines = []
    for ref in refs:
        if ref not in recorded:
            lines.append(f"{ref}: not recorded (registry: {', '.join(now.get(ref, ['?']))})")
        elif ref in now and recorded[ref] != now[ref]:
            lines.append(f"{ref}: recorded {', '.join(recorded[ref])}; registry {', '.join(now[ref])}")
    lines.extend(f"{ref}: recorded, but no template of the catalog uses it" for ref in recorded if ref not in refs)
    return lines


def main(argv: list[str] | None = None, transport: Transport = curl_transport, path: Path = RECORDED,
         refs: list[str] | None = None, users: dict[str, list[str]] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["check", "record", "tags"])
    command = parser.parse_args(argv).command
    if command == "tags":
        users = catalog_users() if users is None else users
        lines, errors = moved_tags(users, transport)
        for error in errors:
            print(f"ERROR {error}", file=sys.stderr)
        for line in lines:
            print(f"MOVED {line}", file=sys.stderr)
        if errors or lines:
            return 1
        print(f"The tags of the {len(users)} pinned images name the indexes they are pinned to.")
        return 0
    refs = catalog_images() if refs is None else refs
    now, errors = query_all(refs, transport)
    for error in errors:
        print(f"ERROR {error}", file=sys.stderr)
    if command == "record":
        if errors:
            print("Nothing recorded: every image must be read.", file=sys.stderr)
            return 1
        path.write_text(render(now))
        print(f"Recorded the platforms of {len(now)} images in {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}.")
        return 0
    lines = differences(load_recorded(path), now, refs)
    for line in lines:
        print(f"DIFFERS {line}", file=sys.stderr)
    if errors or lines:
        return 1
    print(f"The recorded platforms of {len(refs)} images are those of the registries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
