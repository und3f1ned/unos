"""The certificate that the web UI's Caddy serves: a stored one an administrator chose, or the one of
Caddy's local CA (ADR-0025, ADR-0404).

The supervisor hands a stored certificate to Caddy through one file, SERVED_FILE in SERVED_DIR: the
certificate, its chain and its private key in PEM, mode 0600, in the Docker volume that only the
supervisor's and the web UI's containers mount (deploy/docker-compose.yml; read-only in the web
UI's). One file, renamed into place whole, so that Caddy never reads a certificate beside the key of
another. Before the file is written, ``prepare`` checks what Caddy would refuse or browsers reject.
Then ``reload_caddy`` runs `caddy reload` in the web UI's container (docker exec): Caddy adapts
webui/Caddyfile with UNOS_TLS=served (the file) or local (its local CA) and loads it through its
admin endpoint, which listens on that container's own loopback only and keeps the configuration it
runs when the new one fails. At every start of the container its command loads the file again, or
the local CA when the file does not load. ``probe_fingerprint`` asks Caddy itself, in a TLS
handshake, which certificate it serves.

Every function here blocks: call them in a worker thread (ADR-0021).
"""
from __future__ import annotations

import errno
import hashlib
import logging
import os
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from docker.errors import APIError, DockerException, NotFound

from .. import state_files
from ..config import get_settings

logger = logging.getLogger(__name__)

# The volume of the served certificate, as the supervisor's container mounts it (a setting of its own,
# UNOS_SERVED_CERTIFICATE_DIR, ADR-0417), and the one file in it. The web UI's container mounts it
# read-only at the same path, which webui/Caddyfile names.
SERVED_DIR = get_settings().served_certificate_dir
SERVED_FILE = "served.pem"
# The Caddyfile of the web UI's image, which `caddy reload` adapts again.
WEBUI_CADDYFILE = "/etc/caddy/Caddyfile"
# The values of UNOS_TLS that webui/Caddyfile knows: which of its two sites it serves.
TLS_MODES = ("served", "local")
# Where the supervisor reaches the web UI's Caddy over TLS, on the compose network (UNOS_WEBUI_TLS_HOST
# and UNOS_WEBUI_TLS_PORT).
CADDY_TLS_HOST = get_settings().webui_tls_host
CADDY_TLS_PORT = get_settings().webui_tls_port
# Seconds the handshake that shows what Caddy serves may take, the name's lookup included.
PROBE_TIMEOUT = 5
# A certificate that expires within this many days is served with a warning.
EXPIRY_WARNING_DAYS = 30
# The keys that UNOS serves: those that Caddy (Go's TLS) loads and every current browser takes in a
# handshake. RSA keys of at least 2048 bits (the CA/Browser Forum's minimum) and EC keys on P-256
# and P-384. Not P-521, which Chrome does not offer in TLS 1.3, and not Ed25519, which no major
# browser offers: Caddy would load such a certificate and every browser would fail the handshake,
# without a warning to click through and without the local CA to fall back to.
MIN_RSA_BITS = 2048
EC_CURVES = ("secp256r1", "secp384r1")


class CertificateRefused(ValueError):
    """A stored certificate that is not handed to Caddy: why, as the API answers it."""


class CaddyRefused(RuntimeError):
    """`caddy reload` failed: Caddy keeps the configuration it ran. Its error, as Caddy says it."""


class CaddyUnreachable(RuntimeError):
    """The web UI's container, or Docker, could not run `caddy reload`."""


class CaddyTimedOut(CaddyUnreachable):
    """Docker did not answer `caddy reload` in time: whether Caddy loaded the new configuration is
    not known, so the served file is left as the request wrote it (the status then says what Caddy
    serves)."""


@dataclass(frozen=True)
class Prepared:
    """What Caddy gets of a stored certificate: the served file's content, and what the card says."""
    bundle: bytes
    fingerprint: str        # SHA-256 of the certificate, as a TLS handshake shows it
    not_after: datetime
    expires_soon: bool


def fingerprint(der: bytes) -> str:
    """The SHA-256 fingerprint of a certificate in DER, as openssl prints it: AB:CD:..."""
    return ":".join(f"{byte:02X}" for byte in hashlib.sha256(der).digest())


def _der(certificate: x509.Certificate) -> bytes:
    return certificate.public_bytes(serialization.Encoding.DER)


def leaf_fingerprint(pem: bytes) -> str | None:
    """The fingerprint of the first certificate in ``pem``; None when it holds none that parses."""
    try:
        certificates = x509.load_pem_x509_certificates(pem)
    except ValueError:
        return None
    return fingerprint(_der(certificates[0])) if certificates else None


def _subject(certificate: x509.Certificate) -> str:
    return certificate.subject.rfc4514_string()


def _check_dates(certificate: x509.Certificate, what: str, now: datetime) -> None:
    if certificate.not_valid_after_utc <= now:
        raise CertificateRefused(f"{what} expired on {certificate.not_valid_after_utc:%Y-%m-%d %H:%M} UTC")
    if certificate.not_valid_before_utc > now:
        raise CertificateRefused(f"{what} is valid only from {certificate.not_valid_before_utc:%Y-%m-%d %H:%M} UTC")


def _check_key_kind(key) -> None:
    if isinstance(key, rsa.RSAPrivateKey):
        if key.key_size < MIN_RSA_BITS:
            raise CertificateRefused(
                f"The certificate's RSA key has {key.key_size} bits: UNOS serves RSA keys of at least {MIN_RSA_BITS}")
        return
    if isinstance(key, ec.EllipticCurvePrivateKey):
        kind = f"EC {key.curve.name}"
        if key.curve.name in EC_CURVES:
            return
    elif isinstance(key, ed25519.Ed25519PrivateKey):
        kind = "Ed25519"
    else:
        kind = type(key).__name__.removesuffix("PrivateKey").lstrip("_")
    raise CertificateRefused(
        f"The certificate's key is of a kind that browsers cannot use: {kind} (UNOS serves RSA, EC P-256 and P-384)")


def _extension(certificate: x509.Certificate, kind, what: str):
    """The value of the extension ``kind`` of ``certificate``; None without one."""
    try:
        return certificate.extensions.get_extension_for_class(kind).value
    except x509.ExtensionNotFound:
        return None
    except ValueError:
        raise CertificateRefused(f"{what} has extensions that cannot be read") from None


def _check_usage(leaf: x509.Certificate) -> None:
    """Browsers refuse a server certificate whose key usages leave out TLS servers, and offer no way
    past it: one that names its purposes must name serverAuth, one that names its key's uses must
    name digitalSignature (every handshake signs with it)."""
    purposes = _extension(leaf, x509.ExtendedKeyUsage, "The certificate")
    if purposes is not None and not ({x509.ExtendedKeyUsageOID.SERVER_AUTH,
                                      x509.ExtendedKeyUsageOID.ANY_EXTENDED_KEY_USAGE} & set(purposes)):
        raise CertificateRefused("The certificate is not for TLS servers: its extended key usage lacks serverAuth")
    uses = _extension(leaf, x509.KeyUsage, "The certificate")
    if uses is not None and not uses.digital_signature:
        raise CertificateRefused("The certificate's key usage lacks digitalSignature, which a TLS server needs")


def _public(key) -> bytes:
    return key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def prepare(certificate: bytes, key: bytes, chain: bytes | None, now: datetime | None = None) -> Prepared:
    """Check a stored certificate, its key and its chain, and make the served file of them.

    The certificate parses, its private key reads without a passphrase and belongs to it, is of a
    kind that Caddy loads and browsers take (RSA of at least 2048 bits, EC on P-256 or P-384), its
    key usages, when it names them, allow a TLS server, and it is valid now; each certificate of
    the chain is valid now, a CA when it says so, and issued the one before it, by name and
    signature. Certificates after the first in the certificate's own file count as its
    chain, before those of the chain's file; one given twice is sent once. The file holds the
    certificate, its chain in order and the key (PKCS #8), each as cryptography writes it, so that
    the same stored files make the same bytes. Raises CertificateRefused with the reason.
    """
    now = now or datetime.now(timezone.utc)
    try:
        certificates = x509.load_pem_x509_certificates(certificate)
    except ValueError:
        raise CertificateRefused("The certificate cannot be read") from None
    try:
        certificates += x509.load_pem_x509_certificates(chain) if chain and chain.strip() else []
    except ValueError:
        raise CertificateRefused("The certificate chain cannot be read") from None
    try:
        private_key = serialization.load_pem_private_key(key, password=None)
    except TypeError:
        raise CertificateRefused("The private key is protected by a passphrase: Caddy cannot use it") from None
    except (ValueError, UnsupportedAlgorithm):
        raise CertificateRefused("The private key cannot be read") from None

    ordered: list[x509.Certificate] = []
    for each in certificates:
        if all(_der(each) != _der(seen) for seen in ordered):
            ordered.append(each)
    leaf = ordered[0]
    _check_key_kind(private_key)
    try:
        matches = _public(private_key.public_key()) == _public(leaf.public_key())
    except (ValueError, UnsupportedAlgorithm):
        matches = False
    if not matches:
        raise CertificateRefused("The private key does not belong to the certificate")
    _check_usage(leaf)
    _check_dates(leaf, "The certificate", now)
    for number, (child, issuer) in enumerate(zip(ordered, ordered[1:]), start=1):
        try:
            child.verify_directly_issued_by(issuer)
        except (ValueError, TypeError, InvalidSignature, UnsupportedAlgorithm):
            raise CertificateRefused(
                f"The chain is not in order: certificate {number} ({_subject(child)}) is not issued by the next one "
                f"({_subject(issuer)})") from None
        what = f"Certificate {number + 1} of the chain ({_subject(issuer)})"
        _check_dates(issuer, what, now)
        constraints = _extension(issuer, x509.BasicConstraints, what)
        if constraints is not None and not constraints.ca:
            raise CertificateRefused(f"{what} is not a CA")

    bundle = b"".join(c.public_bytes(serialization.Encoding.PEM) for c in ordered) + private_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    not_after = leaf.not_valid_after_utc
    return Prepared(bundle=bundle, fingerprint=fingerprint(_der(leaf)), not_after=not_after,
                    expires_soon=not_after - now <= timedelta(days=EXPIRY_WARNING_DAYS))


# ==================== The served file ====================

def served_path() -> Path:
    return Path(SERVED_DIR) / SERVED_FILE


def read_served() -> bytes | None:
    """The served file's content; None without one (or with a symbolic link at its path, which is
    not followed and which the next write replaces)."""
    try:
        fd = os.open(served_path(), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    except OSError as e:
        if e.errno == errno.ELOOP:
            return None
        raise
    with os.fdopen(fd, "rb") as fh:
        return fh.read()


def _sync_folder() -> None:
    """Sync the volume's folder, so that a rename or a removal in it survives a power cut."""
    fd = os.open(SERVED_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_served(bundle: bytes) -> None:
    """Replace the served file with ``bundle``: written to a temporary file of mode 0600 in the
    volume, synced and renamed into place, so that Caddy reads the old file or the new one, whole."""
    state_files.replace_file(served_path(), bundle, sync=True, mode=0o600)
    _sync_folder()


def remove_served() -> None:
    try:
        served_path().unlink()
    except FileNotFoundError:
        return
    _sync_folder()


# ==================== Caddy ====================

def _docker_client():
    from ..containers.docker import get_client
    return get_client()


def _error_line(output: bytes) -> str | None:
    """The error that `caddy reload` printed last ("Error: ..."), without its prefix."""
    lines = [line.strip() for line in output.decode("utf-8", "replace").splitlines()]
    errors = [line for line in lines if line.startswith("Error:")]
    return errors[-1].removeprefix("Error:").strip() if errors else None


def reload_caddy(tls: str) -> None:
    """Have the web UI's Caddy load webui/Caddyfile again with UNOS_TLS=``tls``.

    `caddy reload --force` in the web UI's container (UNOS_WEBUI_CONTAINER, unos-webui): it reads
    the served file again even when the configuration's text is the same. Blocking: call it in a
    worker thread (ADR-0021); docker-py's timeout bounds it. CaddyRefused when Caddy did not take
    the configuration (it keeps the one it ran), CaddyUnreachable when the container or Docker could
    not run it.
    """
    if tls not in TLS_MODES:
        raise ValueError(f"UNOS_TLS must be one of {TLS_MODES}, not {tls!r}")
    from ..containers.docker import NO_ANSWER, no_answer_detail

    name = get_settings().webui_container
    try:
        client = _docker_client()
        try:
            container = client.containers.get(name)
            result = container.exec_run(
                ["caddy", "reload", "--config", WEBUI_CADDYFILE, "--force"],
                environment={"UNOS_TLS": tls}, stdout=True, stderr=True,
            )
        finally:
            close = getattr(client, "close", None)
            if close:
                close()
    except NotFound as e:
        raise CaddyUnreachable(f"the container {name} does not exist: {e}") from e
    except NO_ANSWER as e:
        raise CaddyUnreachable(no_answer_detail(e)) from e
    except APIError as e:
        raise CaddyUnreachable(f"the container {name} could not run caddy reload: {e}") from e
    except DockerException as e:
        raise CaddyUnreachable(no_answer_detail(e)) from e
    except (requests.exceptions.ReadTimeout, OSError) as e:
        # docker-py's timeout on the exec's answer: requests' ReadTimeout, or the socket's own. (Its
        # errors are OSErrors too, as requests' are: those above are told apart first.)
        raise CaddyTimedOut(
            f"Docker did not answer caddy reload in time ({e}): Caddy may or may not have loaded the change") from e
    if result.exit_code != 0:
        raise CaddyRefused(_error_line(result.output or b"") or f"caddy reload exited with status {result.exit_code}")


def probe_fingerprint() -> str | None:
    """The SHA-256 fingerprint of the certificate that the web UI's Caddy serves, from a TLS
    handshake with it; None when it did not answer one within PROBE_TIMEOUT seconds (each of the
    connection and the handshake).

    The handshake sends no server name, as a browser that opens the NAS by its IP address does, and
    checks nothing: it only reads the certificate. Nothing is written or logged.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((CADDY_TLS_HOST, CADDY_TLS_PORT), timeout=PROBE_TIMEOUT) as raw:
            # No server_hostname: no SNI.
            with context.wrap_socket(raw) as conn:
                der = conn.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError):
        return None
    return fingerprint(der) if der else None
