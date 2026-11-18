"""A stored certificate served by Caddy for the web UI, and Caddy's local CA again
(security.custom-certificates, ADR-0402).

The supervisor checks a stored certificate, writes it with its chain and key into the one file of
the volume that only it and the web UI's container mount, and has the web UI's Caddy load it
again (`caddy reload` through `docker exec`); what Caddy then serves is what a TLS handshake with it
shows. Here a fake Caddy loads that file as the real one does, and a TLS server of this process
stands in for it where the handshake itself is tested.
"""
import asyncio
import socket
import ssl
import stat
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import docker
import pytest
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from src.audit.schemas import AuditAction
from src.auth.utils import verify_token
from src.main import app
from src.security import router as security_router
from src.security import serving

from .test_security import needs_openssl

API = "/api/v1/security/certificates"
NOW = datetime.now(timezone.utc)
# What the fake Caddy serves while it serves its local CA: no stored certificate has it.
LOCAL_CA = "LOCAL:CA"


# ==================== Certificates made for the tests ====================

def _key(kind: str = "ec"):
    if kind == "rsa1024":
        return rsa.generate_private_key(public_exponent=65537, key_size=1024)
    if kind == "rsa":
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return ec.generate_private_key(ec.SECP256R1())


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _certificate(common_name: str, *, key=None, issuer=None, ca: bool = False,
                 not_before: datetime | None = None, not_after: datetime | None = None, extensions=()):
    """A certificate for ``common_name`` and its key: self-signed, or signed by ``issuer`` (a
    SimpleNamespace of cert and key, as this returns)."""
    key = key or _key()
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(common_name))
        .issuer_name(issuer.cert.subject if issuer else _name(common_name))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before or NOW - timedelta(days=1))
        .not_valid_after(not_after or NOW + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(common_name)]), critical=False)
    )
    for extension, critical in extensions:
        builder = builder.add_extension(extension, critical=critical)
    signer = issuer.key if issuer else key
    cert = builder.sign(signer, None if isinstance(signer, ed25519.Ed25519PrivateKey) else hashes.SHA256())
    return SimpleNamespace(
        cert=cert, key=key,
        pem=cert.public_bytes(serialization.Encoding.PEM).decode(),
        key_pem=key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()).decode(),
        fingerprint=serving.fingerprint(cert.public_bytes(serialization.Encoding.DER)),
    )


def _chain():
    """A root CA, an intermediate CA and a leaf for nas.example.com that the intermediate signed."""
    root = _certificate("UNOS Test Root", ca=True)
    intermediate = _certificate("UNOS Test Intermediate", issuer=root, ca=True)
    leaf = _certificate("nas.example.com", issuer=intermediate)
    return root, intermediate, leaf


# ==================== The checks before Caddy gets a certificate ====================

class TestTheChecksBeforeCaddyGetsACertificate:
    """prepare(): the certificate parses, the key matches it, it is valid now, its chain is in
    order, and Caddy can load the result (ADR-0402)."""

    def test_a_self_signed_certificate_becomes_one_file_with_its_key(self):
        made = _certificate("nas.local")

        prepared = serving.prepare(made.pem.encode(), made.key_pem.encode(), None, now=NOW)

        blocks = x509.load_pem_x509_certificates(prepared.bundle)
        assert [b.public_bytes(serialization.Encoding.DER) for b in blocks] == \
            [made.cert.public_bytes(serialization.Encoding.DER)]
        key = serialization.load_pem_private_key(prepared.bundle, password=None)
        assert key.private_numbers() == made.key.private_numbers()
        # The certificate first: Caddy (Go's X509KeyPair) takes the first certificate as the leaf.
        assert prepared.bundle.index(b"BEGIN CERTIFICATE") < prepared.bundle.index(b"BEGIN PRIVATE KEY")
        assert prepared.fingerprint == made.fingerprint
        assert prepared.expires_soon is False

    def test_a_chain_in_order_follows_the_certificate(self):
        root, intermediate, leaf = _chain()

        prepared = serving.prepare(leaf.pem.encode(), leaf.key_pem.encode(),
                                   (intermediate.pem + root.pem).encode(), now=NOW)

        assert [c.subject for c in x509.load_pem_x509_certificates(prepared.bundle)] == \
            [leaf.cert.subject, intermediate.cert.subject, root.cert.subject]

    def test_a_chain_in_the_certificates_own_file_counts_as_its_chain(self):
        root, intermediate, leaf = _chain()

        prepared = serving.prepare((leaf.pem + intermediate.pem).encode(), leaf.key_pem.encode(),
                                   root.pem.encode(), now=NOW)

        assert len(x509.load_pem_x509_certificates(prepared.bundle)) == 3

    def test_a_certificate_given_twice_in_the_chain_is_sent_once(self):
        root, intermediate, leaf = _chain()

        prepared = serving.prepare((leaf.pem + intermediate.pem).encode(), leaf.key_pem.encode(),
                                   (intermediate.pem + root.pem).encode(), now=NOW)

        assert len(x509.load_pem_x509_certificates(prepared.bundle)) == 3

    def test_an_rsa_key_in_its_own_format_is_written_as_pkcs8(self):
        made = _certificate("nas.local", key=_key("rsa"))
        traditional = made.key.private_bytes(serialization.Encoding.PEM,
                                             serialization.PrivateFormat.TraditionalOpenSSL,
                                             serialization.NoEncryption())
        assert b"BEGIN RSA PRIVATE KEY" in traditional

        prepared = serving.prepare(made.pem.encode(), traditional, None, now=NOW)

        assert b"BEGIN PRIVATE KEY" in prepared.bundle and b"RSA PRIVATE KEY" not in prepared.bundle

    @pytest.mark.parametrize("days, soon", [(10, True), (29, True), (31, False), (365, False)])
    def test_a_certificate_that_expires_soon_is_served_with_a_warning(self, days, soon):
        made = _certificate("nas.local", not_after=NOW + timedelta(days=days, minutes=1))

        assert serving.prepare(made.pem.encode(), made.key_pem.encode(), None, now=NOW).expires_soon is soon

    def _refusal(self, certificate: str, key: str, chain: str | None = None) -> str:
        with pytest.raises(serving.CertificateRefused) as refused:
            serving.prepare(certificate.encode(), key.encode(), chain.encode() if chain else None, now=NOW)
        return str(refused.value)

    def test_a_key_of_another_certificate_is_refused(self):
        made, other = _certificate("nas.local"), _certificate("nas.local")

        assert self._refusal(made.pem, other.key_pem) == "The private key does not belong to the certificate"

    def test_text_that_is_no_certificate_is_refused(self):
        made = _certificate("nas.local")

        assert self._refusal("-----BEGIN CERTIFICATE-----\nnot one\n-----END CERTIFICATE-----\n",
                             made.key_pem) == "The certificate cannot be read"
        assert self._refusal("", made.key_pem) == "The certificate cannot be read"

    def test_a_key_that_cannot_be_read_is_refused(self):
        made = _certificate("nas.local")

        assert self._refusal(made.pem, "KEY") == "The private key cannot be read"

    def test_a_key_with_a_passphrase_is_refused(self):
        made = _certificate("nas.local")
        encrypted = made.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.BestAvailableEncryption(b"passphrase")).decode()

        assert self._refusal(made.pem, encrypted) == \
            "The private key is protected by a passphrase: Caddy cannot use it"

    def test_an_expired_certificate_is_refused(self):
        made = _certificate("nas.local", not_before=NOW - timedelta(days=30), not_after=NOW - timedelta(days=1))

        assert self._refusal(made.pem, made.key_pem).startswith("The certificate expired on ")

    def test_a_certificate_valid_only_later_is_refused(self):
        made = _certificate("nas.local", not_before=NOW + timedelta(days=1))

        assert self._refusal(made.pem, made.key_pem).startswith("The certificate is valid only from ")

    def test_a_short_rsa_key_is_refused(self):
        made = _certificate("nas.local", key=_key("rsa1024"))

        assert self._refusal(made.pem, made.key_pem) == \
            "The certificate's RSA key has 1024 bits: UNOS serves RSA keys of at least 2048"

    @pytest.mark.parametrize("key, kind", [
        (lambda: ec.generate_private_key(ec.SECP256K1()), "EC secp256k1"),
        # Caddy loads these, and browsers fail every handshake with them, without a warning to
        # click through: Chrome offers no P-521 in TLS 1.3, no major browser Ed25519.
        (lambda: ec.generate_private_key(ec.SECP521R1()), "EC secp521r1"),
        (lambda: ed25519.Ed25519PrivateKey.generate(), "Ed25519"),
    ], ids=["secp256k1", "P-521", "Ed25519"])
    def test_a_key_that_browsers_cannot_use_is_refused(self, key, kind):
        made = _certificate("nas.local", key=key())

        assert self._refusal(made.pem, made.key_pem) == \
            f"The certificate's key is of a kind that browsers cannot use: {kind} (UNOS serves RSA, EC P-256 and P-384)"

    def test_keys_on_p384_and_rsa_are_served(self):
        for key in (ec.generate_private_key(ec.SECP384R1()), _key("rsa")):
            made = _certificate("nas.local", key=key)
            assert serving.prepare(made.pem.encode(), made.key_pem.encode(), None, now=NOW).fingerprint == \
                made.fingerprint

    def test_a_certificate_for_clients_only_is_refused(self):
        made = _certificate("nas.local", extensions=[
            (x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.CLIENT_AUTH]), False)])

        assert self._refusal(made.pem, made.key_pem) == \
            "The certificate is not for TLS servers: its extended key usage lacks serverAuth"

    def test_a_certificate_for_servers_is_served(self):
        made = _certificate("nas.local", extensions=[
            (x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH, x509.ExtendedKeyUsageOID.CLIENT_AUTH]), False),
            (x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=True,
                           data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
                           encipher_only=False, decipher_only=False), True)])

        assert serving.prepare(made.pem.encode(), made.key_pem.encode(), None, now=NOW).fingerprint == made.fingerprint

    def test_a_key_usage_without_signatures_is_refused(self):
        made = _certificate("nas.local", extensions=[
            (x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=True,
                           data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
                           encipher_only=False, decipher_only=False), True)])

        assert self._refusal(made.pem, made.key_pem) == \
            "The certificate's key usage lacks digitalSignature, which a TLS server needs"

    def test_a_chain_out_of_order_is_refused(self):
        root, intermediate, leaf = _chain()

        assert self._refusal(leaf.pem, leaf.key_pem, root.pem + intermediate.pem) == \
            "The chain is not in order: certificate 1 (CN=nas.example.com) is not issued by the next one " \
            "(CN=UNOS Test Root)"

    def test_a_chain_without_the_issuer_is_refused(self):
        root, intermediate, leaf = _chain()
        stranger = _certificate("Another CA", ca=True)

        assert "is not issued by the next one (CN=Another CA)" in self._refusal(leaf.pem, leaf.key_pem, stranger.pem)

    def test_a_chain_whose_issuer_only_has_the_name_is_refused(self):
        """Same subject, another key: the signature does not hold."""
        root, intermediate, leaf = _chain()
        impostor = _certificate("UNOS Test Intermediate", ca=True)

        assert "is not issued by the next one" in self._refusal(leaf.pem, leaf.key_pem, impostor.pem)

    def test_an_expired_certificate_of_the_chain_is_refused(self):
        root = _certificate("UNOS Test Root", ca=True)
        intermediate = _certificate("UNOS Test Intermediate", issuer=root, ca=True,
                                    not_before=NOW - timedelta(days=30), not_after=NOW - timedelta(days=1))
        leaf = _certificate("nas.example.com", issuer=intermediate)

        assert self._refusal(leaf.pem, leaf.key_pem, intermediate.pem).startswith(
            "Certificate 2 of the chain (CN=UNOS Test Intermediate) expired on ")

    def test_a_chain_certificate_that_is_no_ca_is_refused(self):
        not_a_ca = _certificate("Not a CA")
        leaf = _certificate("nas.example.com", issuer=not_a_ca)

        assert self._refusal(leaf.pem, leaf.key_pem, not_a_ca.pem) == \
            "Certificate 2 of the chain (CN=Not a CA) is not a CA"


# ==================== Serving through the API, with a fake Caddy ====================

def _fingerprint_of_file(path: Path) -> str | None:
    if not path.exists():
        return None
    return serving.leaf_fingerprint(path.read_bytes())


@pytest.fixture
def caddy(tmp_path, monkeypatch):
    """The stored certificates and the served file in a scratch tree, and a fake Caddy: a reload
    loads the served file as the real one does (its first certificate is what it then serves), or
    goes back to the local CA, and a refusal keeps what it served; the TLS handshake shows what it
    serves."""
    certs_dir = tmp_path / "data" / "certs"
    served_dir = tmp_path / "served-certificate"
    served_dir.mkdir()
    monkeypatch.setattr(security_router, "CERTS_DIR", str(certs_dir))
    monkeypatch.setattr(serving, "SERVED_DIR", str(served_dir))
    fake = SimpleNamespace(serving=LOCAL_CA, reloads=[], refuse=None, unreachable=False, probe_fails=False,
                           on_loop=[], file=served_dir / serving.SERVED_FILE, certs=certs_dir)

    def reload_caddy(tls):
        fake.on_loop.append(_on_event_loop())
        if fake.unreachable:
            raise serving.CaddyUnreachable("Docker is not available: no socket")
        fake.reloads.append(tls)
        if fake.refuse:
            raise serving.CaddyRefused(fake.refuse)
        fake.serving = _fingerprint_of_file(fake.file) if tls == "served" else LOCAL_CA

    def probe():
        fake.on_loop.append(_on_event_loop())
        return None if fake.probe_fails else fake.serving

    monkeypatch.setattr(serving, "reload_caddy", reload_caddy)
    monkeypatch.setattr(serving, "probe_fingerprint", probe)
    return fake


@pytest.fixture
def audits(monkeypatch):
    """The audit entries that the certificate routes write."""
    written = []

    async def record(action, request, username, **fields):
        written.append(SimpleNamespace(action=action, username=username, **fields))

    monkeypatch.setattr(security_router, "audit_change", record)
    return written


@pytest.fixture
def admin_api():
    admin = SimpleNamespace(id=1, username="admin", is_admin=True)
    app.dependency_overrides[verify_token] = lambda: admin
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(verify_token, None)


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _store(name: str, made, chain: str | None = None) -> None:
    security_router._store_certificate(name, made.pem, made.key_pem, chain)


def _serve(admin_api, cert_id: str):
    return admin_api.post(f"{API}/{cert_id}/serve")


class TestServingAStoredCertificate:
    def test_serve_hands_the_certificate_to_caddy(self, caddy, audits, admin_api):
        made = _certificate("nas.local")
        _store("nas.local", made)

        answer = _serve(admin_api, "custom_nas.local")

        assert answer.status_code == 200, answer.text
        assert answer.json() == {
            "changed": True, "expires_soon": False,
            "status": {"served_by": "stored_certificate", "served_id": "custom_nas.local",
                       "chosen_id": "custom_nas.local", "fingerprint": made.fingerprint},
        }
        # Docker and the handshake block: never on the event loop (ADR-0021).
        assert caddy.reloads == ["served"] and caddy.on_loop and not any(caddy.on_loop)
        # Only Caddy reads the key: the file is the supervisor's (root's) alone.
        assert stat.S_IMODE(caddy.file.stat().st_mode) == 0o600
        assert serving.leaf_fingerprint(caddy.file.read_bytes()) == made.fingerprint
        assert b"PRIVATE KEY" not in answer.content
        assert [(a.action, a.resource_type, a.resource_id, a.username) for a in audits] == \
            [(AuditAction.SSL_CERT_SERVED, "certificate", "custom_nas.local", "admin")]
        assert audits[0].details == "Browsers get the stored certificate nas.local, until " \
            f"{made.cert.not_valid_after_utc:%Y-%m-%d}"
        status = admin_api.get(f"{API}/status").json()
        assert status["served_by"] == "stored_certificate" and status["served_id"] == "custom_nas.local"

    def test_serving_it_again_writes_and_reloads_nothing(self, caddy, audits, admin_api):
        made = _certificate("nas.local")
        _store("nas.local", made)
        assert _serve(admin_api, "custom_nas.local").status_code == 200
        before = caddy.file.stat()

        again = _serve(admin_api, "custom_nas.local")

        assert again.status_code == 200 and again.json()["changed"] is False
        after = caddy.file.stat()
        assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
        assert caddy.reloads == ["served"]
        assert len(audits) == 1

    def test_a_certificate_that_expires_soon_is_served_and_says_so(self, caddy, audits, admin_api):
        _store("nas.local", _certificate("nas.local", not_after=NOW + timedelta(days=5)))

        answer = _serve(admin_api, "custom_nas.local")

        assert answer.status_code == 200 and answer.json()["expires_soon"] is True
        assert "expires within 30 days" in audits[0].details

    def test_another_stored_certificate_replaces_the_served_one(self, caddy, audits, admin_api):
        first, second = _certificate("nas.local"), _certificate("nas.home.arpa")
        _store("nas.local", first)
        _store("nas.home.arpa", second)
        assert _serve(admin_api, "custom_nas.local").status_code == 200

        answer = _serve(admin_api, "custom_nas.home.arpa")

        assert answer.status_code == 200
        assert answer.json()["status"]["served_id"] == "custom_nas.home.arpa"
        assert caddy.serving == second.fingerprint and caddy.reloads == ["served", "served"]

    def test_a_certificate_that_fails_the_checks_reaches_no_caddy(self, caddy, audits, admin_api):
        made, other = _certificate("nas.local"), _certificate("nas.local")
        security_router._store_certificate("nas.local", made.pem, other.key_pem)

        answer = _serve(admin_api, "custom_nas.local")

        assert answer.status_code == 409
        assert answer.json()["detail"] == \
            "nas.local cannot be served: The private key does not belong to the certificate"
        assert not caddy.file.exists() and caddy.reloads == [] and audits == []

    def test_a_certificate_that_caddy_refuses_leaves_what_it_served(self, caddy, audits, admin_api):
        served, refused = _certificate("nas.local"), _certificate("nas.home.arpa")
        _store("nas.local", served)
        _store("nas.home.arpa", refused)
        assert _serve(admin_api, "custom_nas.local").status_code == 200
        caddy.refuse = "loading certificates: tls: failed to parse private key"

        answer = _serve(admin_api, "custom_nas.home.arpa")

        assert answer.status_code == 502
        assert answer.json()["detail"] == "Caddy did not load nas.home.arpa and serves what it served before: " \
            "loading certificates: tls: failed to parse private key"
        # The file holds what Caddy serves: a restart of the web UI loads it again.
        assert _fingerprint_of_file(caddy.file) == served.fingerprint == caddy.serving
        assert len(audits) == 1

    def test_a_first_certificate_that_caddy_refuses_leaves_no_file(self, caddy, audits, admin_api):
        _store("nas.local", _certificate("nas.local"))
        caddy.refuse = "loading certificates: tls: failed to parse private key"

        assert _serve(admin_api, "custom_nas.local").status_code == 502

        assert not caddy.file.exists() and caddy.serving == LOCAL_CA and audits == []
        assert admin_api.get(f"{API}/status").json() == {
            "served_by": "caddy_local_ca", "served_id": None, "chosen_id": None, "fingerprint": LOCAL_CA}

    def test_a_web_ui_that_cannot_be_reached_changes_nothing(self, caddy, audits, admin_api):
        _store("nas.local", _certificate("nas.local"))
        caddy.unreachable = True

        answer = _serve(admin_api, "custom_nas.local")

        assert answer.status_code == 503
        assert answer.json()["detail"] == "The web UI's Caddy cannot be reached: Docker is not available: no socket"
        assert not caddy.file.exists() and audits == []

    @pytest.mark.parametrize("cert_id, status, detail", [
        ("custom_nas.local", 404, "Certificate not found"),
        ("nas.local", 400, "Invalid certificate id"),
        ("custom_..", 400, "Invalid certificate id"),
        ("custom_a b", 400, "Invalid certificate id"),
    ])
    def test_unknown_ids_are_refused(self, caddy, audits, admin_api, cert_id, status, detail):
        answer = _serve(admin_api, cert_id)

        assert (answer.status_code, answer.json()["detail"]) == (status, detail)
        assert caddy.reloads == [] and audits == []

    def test_a_regular_user_may_not_serve_a_certificate(self, caddy, audits):
        _store("nas.local", _certificate("nas.local"))
        user = SimpleNamespace(id=2, username="carol", is_admin=False)
        app.dependency_overrides[verify_token] = lambda: user
        try:
            client = TestClient(app)
            assert client.post(f"{API}/custom_nas.local/serve").status_code == 403
            assert client.post(f"{API}/caddy_local_ca/serve").status_code == 403
            assert client.get(f"{API}/status").status_code == 403
        finally:
            app.dependency_overrides.pop(verify_token, None)
        assert caddy.reloads == [] and not caddy.file.exists()


class TestCaddysLocalCaAgain:
    def test_use_caddys_local_ca_removes_the_served_file(self, caddy, audits, admin_api):
        _store("nas.local", _certificate("nas.local"))
        assert _serve(admin_api, "custom_nas.local").status_code == 200

        answer = _serve(admin_api, "caddy_local_ca")

        assert answer.status_code == 200
        assert answer.json() == {"changed": True, "expires_soon": False, "status": {
            "served_by": "caddy_local_ca", "served_id": None, "chosen_id": None, "fingerprint": LOCAL_CA}}
        assert not caddy.file.exists() and caddy.reloads == ["served", "local"]
        assert [(a.action, a.resource_id, a.details) for a in audits][1:] == \
            [(AuditAction.SSL_CERT_SERVED, "caddy_local_ca", "Browsers get the certificate of Caddy's local CA")]

    def test_the_local_ca_again_changes_nothing_while_it_is_served(self, caddy, audits, admin_api):
        answer = _serve(admin_api, "caddy_local_ca")

        assert answer.status_code == 200 and answer.json()["changed"] is False
        assert caddy.reloads == [] and audits == []

    def test_a_refused_reload_keeps_the_served_file(self, caddy, audits, admin_api):
        made = _certificate("nas.local")
        _store("nas.local", made)
        assert _serve(admin_api, "custom_nas.local").status_code == 200
        caddy.refuse = "adapting config using caddyfile: some error"

        answer = _serve(admin_api, "caddy_local_ca")

        assert answer.status_code == 502
        assert answer.json()["detail"] == "Caddy did not load its local CA's certificate and serves what it " \
            "served before: adapting config using caddyfile: some error"
        assert _fingerprint_of_file(caddy.file) == made.fingerprint == caddy.serving
        assert len(audits) == 1


class TestDeletingTheServedCertificate:
    def test_deleting_it_returns_the_web_ui_to_the_local_ca(self, caddy, audits, admin_api):
        _store("nas.local", _certificate("nas.local"))
        assert _serve(admin_api, "custom_nas.local").status_code == 200

        answer = admin_api.delete(f"{API}/custom_nas.local")

        assert answer.status_code == 200, answer.text
        assert not caddy.file.exists() and caddy.reloads == ["served", "local"] and caddy.serving == LOCAL_CA
        assert sorted(p.name for p in caddy.certs.iterdir()) == []
        assert [(a.action, a.resource_id) for a in audits] == [
            (AuditAction.SSL_CERT_SERVED, "custom_nas.local"),
            (AuditAction.SSL_CERT_SERVED, "caddy_local_ca"),
            (AuditAction.SSL_CERT_DELETED, "custom_nas.local"),
        ]
        assert audits[1].details == "Browsers get the certificate of Caddy's local CA: the served nas.local " \
            "was deleted"

    def test_a_delete_whose_reload_fails_deletes_nothing(self, caddy, audits, admin_api):
        made = _certificate("nas.local")
        _store("nas.local", made)
        assert _serve(admin_api, "custom_nas.local").status_code == 200
        caddy.refuse = "some error"

        answer = admin_api.delete(f"{API}/custom_nas.local")

        assert answer.status_code == 502
        assert sorted(p.name for p in caddy.certs.iterdir()) == ["nas.local.crt", "nas.local.key"]
        assert _fingerprint_of_file(caddy.file) == made.fingerprint
        assert len(audits) == 1

    def test_deleting_another_certificate_leaves_caddy_alone(self, caddy, audits, admin_api):
        _store("nas.local", _certificate("nas.local"))
        _store("nas.home.arpa", _certificate("nas.home.arpa"))
        assert _serve(admin_api, "custom_nas.local").status_code == 200

        assert admin_api.delete(f"{API}/custom_nas.home.arpa").status_code == 200

        assert caddy.reloads == ["served"] and caddy.file.exists()
        assert [a.action for a in audits] == [AuditAction.SSL_CERT_SERVED, AuditAction.SSL_CERT_DELETED]


class TestTheServedCertificateIsNotReplaced:
    """A store under the served certificate's name would leave Caddy serving a copy that no stored
    certificate matches: it is refused until another certificate or the local CA is served."""

    def test_a_store_under_the_served_name_is_refused(self, caddy, admin_api):
        made = _certificate("nas.local")
        _store("nas.local", made)
        assert _serve(admin_api, "custom_nas.local").status_code == 200

        with pytest.raises(security_router.CertificateServed):
            _store("nas.local", _certificate("nas.local"))

        assert serving.leaf_fingerprint((caddy.certs / "nas.local.crt").read_bytes()) == made.fingerprint

    def test_a_generation_under_the_served_name_is_refused(self, caddy, admin_api, monkeypatch):
        made = _certificate("nas.local")
        _store("nas.local", made)
        assert _serve(admin_api, "custom_nas.local").status_code == 200
        other = _certificate("nas.local")
        printed = other.key_pem + other.pem
        monkeypatch.setattr(security_router.subprocess, "run",
                            lambda cmd, **kw: security_router.subprocess.CompletedProcess(cmd, 0, printed, ""))

        answer = admin_api.post(f"{API}/self-signed", json={"domain": "nas.local"})

        assert answer.status_code == 409
        assert answer.json()["detail"] == "nas.local is the chosen served certificate: serve another certificate or " \
            "Caddy's local CA " \
            "before you replace it"
        assert serving.leaf_fingerprint((caddy.certs / "nas.local.crt").read_bytes()) == made.fingerprint


class TestWhatTheStatusSays:
    def test_a_chosen_certificate_that_caddy_did_not_load_is_named_as_chosen_only(self, caddy, admin_api):
        """A restart of the web UI that could not load the served file falls back to the local CA:
        the status says what browsers get, and which certificate was chosen."""
        made = _certificate("nas.local")
        _store("nas.local", made)
        assert _serve(admin_api, "custom_nas.local").status_code == 200
        caddy.serving = LOCAL_CA

        assert admin_api.get(f"{API}/status").json() == {
            "served_by": "caddy_local_ca", "served_id": None, "chosen_id": "custom_nas.local",
            "fingerprint": LOCAL_CA}

    def test_a_caddy_that_does_not_answer_is_named_as_unknown(self, caddy, admin_api):
        caddy.probe_fails = True

        assert admin_api.get(f"{API}/status").json() == {
            "served_by": "unknown", "served_id": None, "chosen_id": None, "fingerprint": None}

    @needs_openssl
    def test_the_listing_marks_the_served_and_the_chosen_certificate(self, caddy, admin_api):
        served, other = _certificate("nas.local"), _certificate("nas.home.arpa", not_after=NOW + timedelta(days=3))
        _store("nas.local", served)
        _store("nas.home.arpa", other)
        assert _serve(admin_api, "custom_nas.local").status_code == 200

        listing = admin_api.get(API).json()

        assert listing["status"]["served_by"] == "stored_certificate"
        by_id = {c["id"]: c for c in listing["certificates"]}
        assert (by_id["custom_nas.local"]["served"], by_id["custom_nas.local"]["chosen"]) == (True, True)
        assert (by_id["custom_nas.home.arpa"]["served"], by_id["custom_nas.home.arpa"]["chosen"]) == (False, False)
        # SHA-256, as the handshake's certificate is compared, and openssl s_client prints it.
        assert by_id["custom_nas.local"]["fingerprint"] == served.fingerprint
        assert (by_id["custom_nas.local"]["expires_soon"], by_id["custom_nas.home.arpa"]["expires_soon"]) == \
            (False, True)


class TestOnlyRealChangesAndWhatCaddyServes:
    def test_a_chosen_certificate_that_caddy_did_not_load_is_loaded_again(self, caddy, audits, admin_api):
        """Serve again what the file holds while Caddy serves the local CA (a start that could not
        load it): Caddy reloads, and the file is not written again."""
        made = _certificate("nas.local")
        _store("nas.local", made)
        assert _serve(admin_api, "custom_nas.local").status_code == 200
        caddy.serving = LOCAL_CA
        before = caddy.file.stat()

        answer = _serve(admin_api, "custom_nas.local")

        assert answer.status_code == 200 and answer.json()["changed"] is True
        assert caddy.reloads == ["served", "served"] and caddy.serving == made.fingerprint
        after = caddy.file.stat()
        assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
        assert len(audits) == 2

    def test_a_refused_reload_of_the_unchanged_file_does_not_write_it_again(self, caddy, audits, admin_api):
        _store("nas.local", _certificate("nas.local"))
        assert _serve(admin_api, "custom_nas.local").status_code == 200
        caddy.serving = LOCAL_CA
        caddy.refuse = "some error"
        before = caddy.file.stat()

        assert _serve(admin_api, "custom_nas.local").status_code == 502

        after = caddy.file.stat()
        assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
        assert len(audits) == 1

    def test_a_caddy_that_cannot_be_asked_is_not_reloaded_for_the_same_file(self, caddy, audits, admin_api):
        _store("nas.local", _certificate("nas.local"))
        assert _serve(admin_api, "custom_nas.local").status_code == 200
        caddy.probe_fails = True

        answer = _serve(admin_api, "custom_nas.local")

        assert answer.status_code == 200 and answer.json()["changed"] is False
        assert caddy.reloads == ["served"] and len(audits) == 1

    def test_two_serves_at_once_reload_caddy_once(self, caddy, monkeypatch):
        """The handshake that decides is made under the lock: the second serve sees the first's."""
        _store("nas.local", _certificate("nas.local"))
        reload = serving.reload_caddy
        started = threading.Event()

        def slow_reload(tls):
            started.set()
            threading.Event().wait(0.3)
            reload(tls)

        monkeypatch.setattr(serving, "reload_caddy", slow_reload)
        results = []
        threads = [threading.Thread(target=lambda: results.append(security_router._serve_stored("nas.local")[0]))
                   for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)

        assert sorted(results) == [False, True] and caddy.reloads == ["served"]

    def test_a_reload_that_docker_did_not_answer_in_time_keeps_the_new_file(self, caddy, audits, admin_api,
                                                                         monkeypatch):
        """Caddy may have loaded it: the file holds the certificate asked for, and the status says
        what Caddy serves."""
        made = _certificate("nas.local")
        _store("nas.local", made)

        def timed_out(tls):
            caddy.reloads.append(tls)
            raise serving.CaddyTimedOut("Docker did not answer caddy reload in time (timed out)")

        monkeypatch.setattr(serving, "reload_caddy", timed_out)

        answer = _serve(admin_api, "custom_nas.local")

        assert answer.status_code == 503
        assert _fingerprint_of_file(caddy.file) == made.fingerprint and audits == []
        assert admin_api.get(f"{API}/status").json()["chosen_id"] == "custom_nas.local"

    def test_deleting_what_caddy_serves_reloads_it_although_the_file_holds_another(self, caddy, audits, admin_api):
        """After a reload that Docker did not answer in time and Caddy then loaded, Caddy can serve a
        certificate that the served file does not hold: its delete loads the file's again."""
        chosen, other = _certificate("nas.local"), _certificate("nas.home.arpa")
        _store("nas.local", chosen)
        _store("nas.home.arpa", other)
        assert _serve(admin_api, "custom_nas.local").status_code == 200
        caddy.serving = other.fingerprint

        assert admin_api.delete(f"{API}/custom_nas.home.arpa").status_code == 200

        assert caddy.reloads == ["served", "served"] and caddy.serving == chosen.fingerprint
        assert [(a.action, a.resource_id) for a in audits][1:] == [
            (AuditAction.SSL_CERT_SERVED, "custom_nas.local"),
            (AuditAction.SSL_CERT_DELETED, "custom_nas.home.arpa"),
        ]

    def test_the_local_ca_again_reloads_a_caddy_that_serves_a_stored_certificate_without_a_file(
            self, caddy, audits, admin_api):
        made = _certificate("nas.local")
        _store("nas.local", made)
        caddy.serving = made.fingerprint

        answer = _serve(admin_api, "caddy_local_ca")

        assert answer.status_code == 200 and answer.json()["changed"] is True
        assert caddy.reloads == ["local"] and caddy.serving == LOCAL_CA


# ==================== Audit entries of stores and deletes ====================

@needs_openssl
class TestStoresAndDeletesAreAudited:
    def test_an_upload_a_generation_and_a_delete(self, caddy, audits, admin_api):
        made = _certificate("nas.local")

        assert admin_api.post(f"{API}/upload", json={"certificate": made.pem, "private_key": made.key_pem}) \
            .status_code == 200
        assert admin_api.post(f"{API}/self-signed", json={"domain": "nas.home.arpa"}).status_code == 200
        assert admin_api.delete(f"{API}/custom_nas.local").status_code == 200

        assert [(a.action, a.resource_type, a.resource_id) for a in audits] == [
            (AuditAction.SSL_CERT_UPLOADED, "certificate", "custom_nas.local"),
            (AuditAction.SSL_CERT_GENERATED, "certificate", "custom_nas.home.arpa"),
            (AuditAction.SSL_CERT_DELETED, "certificate", "custom_nas.local"),
        ]

    def test_refused_changes_write_no_entry(self, caddy, audits, admin_api):
        assert admin_api.post(f"{API}/upload", json={"certificate": "x", "private_key": "y"}).status_code == 400
        assert admin_api.delete(f"{API}/custom_nas.local").status_code == 404

        assert audits == []


# ==================== Caddy's reload through docker exec ====================

class FakeContainer:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def exec_run(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class FakeDocker:
    def __init__(self, container):
        self.container = container
        self.names = []
        self.containers = self

    def get(self, name):
        self.names.append(name)
        if self.container is None:
            raise docker.errors.NotFound(f"No such container: {name}")
        return self.container

    def close(self):
        pass


@pytest.fixture
def docker_client(monkeypatch):
    def install(result=None, *, container=True, client_error=None):
        fake = FakeDocker(FakeContainer(result) if container else None)

        def client():
            if client_error:
                raise client_error
            return fake

        monkeypatch.setattr(serving, "_docker_client", client)
        return fake
    return install


class TestCaddyReload:
    """caddy reload in the web UI's container, with the configuration of the chosen TLS: Caddy
    adapts the Caddyfile with UNOS_TLS set and loads it through its admin endpoint on the
    container's own loopback, which keeps what it served when the new one fails (ADR-0402)."""

    @pytest.mark.parametrize("tls", ["served", "local"])
    def test_the_reload_runs_in_the_web_uis_container(self, docker_client, tls):
        fake = docker_client(docker.models.containers.ExecResult(0, b"{\"level\":\"info\"}\n"))

        serving.reload_caddy(tls)

        assert fake.names == ["unos-webui"]
        [(cmd, kwargs)] = fake.container.calls
        assert cmd == ["caddy", "reload", "--config", "/etc/caddy/Caddyfile", "--force"]
        assert kwargs["environment"] == {"UNOS_TLS": tls}

    def test_another_tls_is_refused_before_docker(self, docker_client):
        fake = docker_client(docker.models.containers.ExecResult(0, b""))

        with pytest.raises(ValueError):
            serving.reload_caddy("anything")
        assert fake.names == []

    def test_caddys_refusal_names_its_error(self, docker_client):
        output = (b'{"level":"info","msg":"using config from file"}\n'
                  b'Error: sending configuration to instance: caddy responded with error: HTTP 400: '
                  b'{"error":"loading config: loading new config: loading tls app module: provision tls: '
                  b'loading certificates: tls: private key does not match public key"}\n')
        docker_client(docker.models.containers.ExecResult(1, output))

        with pytest.raises(serving.CaddyRefused) as refused:
            serving.reload_caddy("served")

        assert str(refused.value) == "sending configuration to instance: caddy responded with error: HTTP 400: " \
            '{"error":"loading config: loading new config: loading tls app module: provision tls: ' \
            'loading certificates: tls: private key does not match public key"}'

    def test_a_refusal_without_an_error_line_names_its_exit_status(self, docker_client):
        docker_client(docker.models.containers.ExecResult(137, b""))

        with pytest.raises(serving.CaddyRefused, match="^caddy reload exited with status 137$"):
            serving.reload_caddy("served")

    @pytest.mark.parametrize("install, message", [
        (dict(container=False), "the container unos-webui does not exist: "),
        (dict(client_error=docker.errors.DockerException("no socket")), "Docker is not available: no socket"),
        (dict(result=requests.exceptions.ConnectionError("refused")), "Docker is not available: refused"),
    ], ids=["no container", "no docker", "no answer"])
    def test_a_container_or_docker_that_cannot_be_reached(self, docker_client, install, message):
        docker_client(**install)

        with pytest.raises(serving.CaddyUnreachable) as unreachable:
            serving.reload_caddy("served")

        assert str(unreachable.value).startswith(message)


# ==================== The TLS handshake that shows what Caddy serves ====================

@pytest.fixture
def tls_server(tmp_path, monkeypatch):
    """A TLS server of this process in the web UI's Caddy's place: it records the SNI of each
    handshake."""
    made = _certificate("nas.local")
    (tmp_path / "server.pem").write_text(made.pem + made.key_pem)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / "server.pem")
    names = []
    context.sni_callback = lambda sock, name, ctx: names.append(name)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    stop = threading.Event()

    def serve():
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                continue
            conn.settimeout(5)
            try:
                with context.wrap_socket(conn, server_side=True):
                    pass
            except (OSError, ssl.SSLError):
                pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    monkeypatch.setattr(serving, "CADDY_TLS_HOST", "127.0.0.1")
    monkeypatch.setattr(serving, "CADDY_TLS_PORT", listener.getsockname()[1])
    yield SimpleNamespace(fingerprint=made.fingerprint, names=names)
    stop.set()
    thread.join(5)
    listener.close()


class TestTheHandshake:
    def test_the_probe_names_the_served_certificates_fingerprint(self, tls_server):
        assert serving.probe_fingerprint() == tls_server.fingerprint
        # No name, as a browser that opens the NAS by its IP address sends none: Caddy answers it
        # as its default_sni.
        assert tls_server.names == [None]

    def test_a_caddy_that_does_not_answer_gives_no_fingerprint(self, monkeypatch):
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
        closed.close()
        monkeypatch.setattr(serving, "CADDY_TLS_HOST", "127.0.0.1")
        monkeypatch.setattr(serving, "CADDY_TLS_PORT", port)

        assert serving.probe_fingerprint() is None


# ==================== The served file ====================

class TestTheServedFile:
    def test_it_is_written_whole_with_mode_0600_and_no_temporary_file_stays(self, tmp_path, monkeypatch):
        monkeypatch.setattr(serving, "SERVED_DIR", str(tmp_path))

        serving.write_served(b"bundle")

        assert sorted(p.name for p in tmp_path.iterdir()) == [serving.SERVED_FILE]
        assert stat.S_IMODE((tmp_path / serving.SERVED_FILE).stat().st_mode) == 0o600
        assert serving.read_served() == b"bundle"

    def test_a_symbolic_link_at_its_path_is_replaced_not_followed(self, tmp_path, monkeypatch):
        served = tmp_path / "served"
        served.mkdir()
        target = tmp_path / "elsewhere"
        target.write_bytes(b"kept")
        (served / serving.SERVED_FILE).symlink_to(target)
        monkeypatch.setattr(serving, "SERVED_DIR", str(served))

        assert serving.read_served() is None
        serving.write_served(b"bundle")

        assert target.read_bytes() == b"kept"
        assert not (served / serving.SERVED_FILE).is_symlink()

    def test_remove_leaves_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(serving, "SERVED_DIR", str(tmp_path))
        serving.write_served(b"bundle")

        serving.remove_served()
        serving.remove_served()

        assert serving.read_served() is None and list(tmp_path.iterdir()) == []
