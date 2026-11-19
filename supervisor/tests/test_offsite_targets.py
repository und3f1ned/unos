"""The off-device copy to a restic REST server and to an SFTP server (backup.off-device-copies-over-sftp,
ADR-0422), against the fake restic of fake_restic.py, which plays both servers.

* A REST server: https:// only, its user and password, which restic gets only in its environment
  (RESTIC_REST_USERNAME, RESTIC_REST_PASSWORD) and no answer, file but the secrets file, log line or
  argument holds; its certificate checked by the system's roots, a CA certificate in PEM, or the
  server's own certificate pinned by its SHA-256 fingerprint, which restic gets as --cacert from a
  file of TMPDIR that is gone after each command; a server that does not answer refused at once.
* SFTP: a key pair that UNOS makes, whose private key stays in the secrets file and reaches ssh
  only as a file of mode 0600 in TMPDIR while restic runs; the host key pinned at the first test
  (StrictHostKeyChecking accept-new once, yes ever after) and refused when it changes, until a save
  trusts the new one; a fingerprint typed beforehand checked before restic makes anything; ssh run
  through restic's -o sftp.command with a fixed argv of the host's ssh (nsenter into the host's
  mount and network namespaces, the files through /proc/<pid>/root), no password and no option
  that a value could add; settings edited by hand refused before ssh runs.
* Both: the copy, its check and restore test, the snapshots and both restores, as for a disk.
"""

from __future__ import annotations

import base64
import contextlib
import datetime as dt
import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
import ssl
import stat
import threading
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import HTTPException

from . import fake_restic
from .test_offsite import (  # noqa: F401  the fixtures env and client
    API,
    PASSWORD,
    REAL_PROBE_STORE,
    calls,
    client,
    command_of,
    commands,
    env,
    option,
    wait_until_idle,
)
from .test_offsite import offsite, runner  # noqa: F401

# The fixtures of test_offsite.py that these tests take by name.
FIXTURES = (env, client)

host_layer = __import__("src.host", fromlist=["host"])

REST_PASSWORD = "the REST server's password"
REST = "https://nas.example.com:8000"


def rest_settings(env, **changes) -> dict:
    return {"kind": "rest", "endpoint": REST, "prefix": "unos", "username": "family", "rest_password": REST_PASSWORD,
            "password": PASSWORD, "sources": [str(env.pool)], "limit_upload_kib": 500, **changes}


def sftp_settings(env, **changes) -> dict:
    return {"kind": "sftp", "host": "backup.example.com", "port": 2222, "username": "unos", "path": "/srv/copies/unos",
            "password": PASSWORD, "sources": [str(env.pool)], **changes}


async def save(env, **settings) -> dict:
    return await offsite.save_settings(offsite.SettingsRequest(**settings))


def rest_log(env) -> list[dict]:
    path = env.state / "rest.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def ssh_log(env) -> list[dict]:
    path = env.state / "ssh.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def sftp_server(env, host="backup.example.com", port=2222) -> Path:
    return env.state / "sftp" / f"{host}_{port}"


def server_key(env, **where) -> str:
    folder = sftp_server(env, **where)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "host_key"
    if not path.exists():
        path.write_text(fake_restic.ssh_key_line(folder.name.encode()) + "\n")
    return path.read_text().strip()


def fingerprint(key_line: str) -> str:
    blob = base64.b64decode(key_line.split()[1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def ssh_command(call: dict) -> list[str]:
    commands_given = [value for value in option(call["argv"], "-o") if value.startswith("sftp.command=")]
    assert len(commands_given) == 1, call["argv"]
    return commands_given[0].removeprefix("sftp.command=").split(" ")


def ssh_option(argv: list[str], name: str) -> str:
    found = [value.split("=", 1)[1] for value in option(argv, "-o") if value.split("=", 1)[0] == name]
    assert len(found) == 1, (name, argv)
    return found[0]


@pytest.fixture
def key(client):
    """The key pair of UNOS, made through the API: its public key."""
    answer = client.post(f"{API}/sftp-key")
    assert answer.status_code == 200, answer.text
    return answer.json()["sftp_public_key"]


@pytest.fixture(autouse=True)
def host_has_ssh(monkeypatch):
    monkeypatch.setattr(host_layer, "missing_host_binaries", lambda names, **kwargs: [])


# --- a REST server --------------------------------------------------------------------------------


async def test_saving_a_rest_server_passes_its_credentials_only_in_restics_environment(env):
    answer = await save(env, **rest_settings(env))

    assert answer["configured"] and answer["has_password"] and answer["has_rest_password"]
    stored = answer["settings"]
    assert (stored["kind"], stored["endpoint"], stored["prefix"], stored["username"], stored["tls"]) == (
        "rest", REST, "unos", "family", "system")
    assert commands(env) == ["cat", "init"]
    for call in calls(env):
        assert call["env"]["RESTIC_REPOSITORY"] == f"rest:{REST}/unos/"
        assert (call["env"]["RESTIC_REST_USERNAME"], call["env"]["RESTIC_REST_PASSWORD"]) == ("family", REST_PASSWORD)
        assert REST_PASSWORD not in " ".join(call["argv"]) and "--cacert" not in call["argv"]
        # A store over the network: restic's cache in TMPDIR and the limits, as for S3.
        assert option(call["argv"], "--cache-dir") and option(call["argv"], "--limit-upload") == ["500"]
    assert (env.state / "rest" / "nas.example.com_8000" / "repos" / "unos" / "config").is_file()
    assert REST_PASSWORD not in json.dumps(answer)
    assert REST_PASSWORD not in (env.data / "offsite.json").read_text()
    secrets_file = env.data / "offsite_secrets.json"
    assert stat.S_IMODE(secrets_file.stat().st_mode) == 0o600
    assert json.loads(secrets_file.read_text())["rest_password"] == REST_PASSWORD


async def test_a_wrong_rest_password_answers_400_without_it_and_saves_nothing(env):
    server = env.state / "rest" / "nas.example.com_8000"
    server.mkdir(parents=True)
    (server / "credentials.json").write_text(json.dumps({"username": "family", "password": "another one"}))

    with pytest.raises(HTTPException) as refused:
        await save(env, **rest_settings(env))

    assert refused.value.status_code == 400 and "401 Unauthorized" in refused.value.detail
    assert REST_PASSWORD not in refused.value.detail and PASSWORD not in refused.value.detail
    assert not (env.data / "offsite.json").exists() and not (env.data / "offsite_secrets.json").exists()


async def test_an_edit_keeps_the_rest_password_of_the_same_server_and_user_only(env):
    await save(env, **rest_settings(env))

    kept = await save(env, **rest_settings(env, rest_password=None, password=None, check_percent=4))
    assert kept["has_rest_password"] and kept["settings"]["check_percent"] == 4
    assert calls(env)[-1]["env"]["RESTIC_REST_PASSWORD"] == REST_PASSWORD

    with pytest.raises(HTTPException) as refused:
        await save(env, **rest_settings(env, username="another", rest_password=None, password=None))
    assert refused.value.status_code == 400 and "password of the REST server" in refused.value.detail


def _certificate(name: str = "127.0.0.1") -> tuple[bytes, bytes]:
    """A self-signed certificate for the IP address NAME, and its key, in PEM."""
    private = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = dt.datetime.now(dt.UTC)
    certificate = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(private.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(name))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(private, hashes.SHA256())
    )
    return (certificate.public_bytes(serialization.Encoding.PEM),
            private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))


@contextlib.contextmanager
def tls_server(tmp_path: Path, certificate: bytes, key_pem: bytes):
    """A TLS listener on 127.0.0.1 that makes the handshake with CERTIFICATE and closes: its port."""
    (tmp_path / "server.pem").write_bytes(certificate)
    (tmp_path / "server.key").write_bytes(key_pem)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / "server.pem", tmp_path / "server.key")
    server = socket.create_server(("127.0.0.1", 0))
    server.settimeout(0.1)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except (TimeoutError, OSError):
                continue
            with contextlib.suppress(OSError, ssl.SSLError):
                conn.settimeout(5)
                with context.wrap_socket(conn, server_side=True):
                    pass
            with contextlib.suppress(OSError):
                conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield server.getsockname()[1]
    finally:
        stop.set()
        thread.join()
        server.close()


def _sha256(pem: bytes) -> str:
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem.decode())).hexdigest()


async def test_a_pinned_fingerprint_stores_the_servers_certificate_for_restics_cacert(env, monkeypatch):
    monkeypatch.setattr(offsite, "probe_store", REAL_PROBE_STORE, raising=False)
    certificate, key_pem = _certificate()
    digest = _sha256(certificate)
    with tls_server(env.tmp, certificate, key_pem) as port:
        endpoint = f"https://127.0.0.1:{port}"
        server = env.state / "rest" / f"127.0.0.1_{port}"
        server.mkdir(parents=True)
        (server / "certificate.pem").write_text(certificate.decode())
        colons = ":".join(digest[i:i + 2] for i in range(0, 64, 2)).upper()

        answer = await save(env, **rest_settings(env, endpoint=endpoint, tls="fingerprint", certificate_sha256=colons))

        stored = answer["settings"]
        assert (stored["tls"], stored["certificate_sha256"]) == ("fingerprint", digest)
        assert ssl.PEM_cert_to_DER_cert(stored["certificate"]) == ssl.PEM_cert_to_DER_cert(certificate.decode())
        logged = rest_log(env)
        assert logged and all(entry["cacert"].strip() == stored["certificate"].strip() for entry in logged)
        # The file of --cacert lives in TMPDIR for the command only.
        assert all(not os.path.exists(entry["cacert_path"]) for entry in logged)

        ok, message = await offsite.run_copy("manual")
        assert ok, message

    # Another certificate on the server: the run fails before restic runs, naming both fingerprints.
    other, other_key = _certificate()
    (env.tmp / "other").mkdir()
    with tls_server(env.tmp / "other", other, other_key) as port2:
        stored_file = json.loads((env.data / "offsite.json").read_text())
        stored_file["endpoint"] = f"https://127.0.0.1:{port2}"
        (env.data / "offsite.json").write_text(json.dumps(stored_file))
        before = len(calls(env))
        ok, message = await offsite.run_copy("manual")
    assert not ok and "not the pinned" in message and _sha256(other)[:2].upper() in message, message
    assert len(calls(env)) == before


async def test_a_fingerprint_that_the_server_does_not_have_is_refused_and_nothing_saved(env, monkeypatch):
    monkeypatch.setattr(offsite, "probe_store", REAL_PROBE_STORE, raising=False)
    certificate, key_pem = _certificate()
    with tls_server(env.tmp, certificate, key_pem) as port:
        with pytest.raises(HTTPException) as refused:
            await save(env, **rest_settings(env, endpoint=f"https://127.0.0.1:{port}", tls="fingerprint",
                                            certificate_sha256="ab" * 32))
    assert refused.value.status_code == 400
    assert "AB:AB" in refused.value.detail and _sha256(certificate)[:2].upper() in refused.value.detail
    assert calls(env) == [] and not (env.data / "offsite.json").exists()


async def test_a_ca_certificate_reaches_restic_as_cacert(env):
    certificate, _ = _certificate()
    server = env.state / "rest" / "nas.example.com_8000"
    server.mkdir(parents=True)
    (server / "certificate.pem").write_text(certificate.decode())

    answer = await save(env, **rest_settings(env, tls="ca", certificate=certificate.decode()))

    assert answer["settings"]["tls"] == "ca" and answer["settings"]["certificate"].strip() == certificate.decode().strip()
    assert [entry["cacert"].strip() for entry in rest_log(env)] == [certificate.decode().strip()] * 2
    # Without it the server's certificate is refused, as restic refuses one of no root it trusts.
    (env.data / "offsite.json").unlink()
    with pytest.raises(HTTPException) as refused:
        await save(env, **rest_settings(env, tls="system"))
    assert refused.value.status_code == 400 and "certificate signed by unknown authority" in refused.value.detail


def _authority() -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    private = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "A CA of the family")])
    now = dt.datetime.now(dt.UTC)
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(private.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(private, hashes.SHA256())
    )
    return certificate, private


def _issued(authority, authority_key, *, days: int = 30) -> tuple[bytes, bytes]:
    """A certificate for 127.0.0.1 that AUTHORITY signed, valid from yesterday for DAYS more days."""
    private = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.UTC)
    certificate = (
        x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
        .issuer_name(authority.subject).public_key(private.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(authority_key, hashes.SHA256())
    )
    return (certificate.public_bytes(serialization.Encoding.PEM),
            private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))


async def test_a_ca_of_ones_own_is_the_only_root_of_the_probe(env, monkeypatch):
    monkeypatch.setattr(offsite, "probe_store", REAL_PROBE_STORE, raising=False)
    authority, authority_key = _authority()
    authority_pem = authority.public_bytes(serialization.Encoding.PEM).decode()
    leaf, leaf_key = _issued(authority, authority_key)
    with tls_server(env.tmp, leaf, leaf_key) as port:
        endpoint = f"https://127.0.0.1:{port}"
        server = env.state / "rest" / f"127.0.0.1_{port}"
        server.mkdir(parents=True)
        (server / "ca.pem").write_text(authority_pem)
        # Signed by the CA given: it answers, and restic gets that CA.
        answer = await save(env, **rest_settings(env, endpoint=endpoint, tls="ca", certificate=authority_pem))
        assert answer["configured"] and rest_log(env)[-1]["cacert"].strip() == authority_pem.strip()
        (env.data / "offsite.json").unlink()
        # Another CA: refused before restic runs; so is the system's trust for it.
        other, _ = _authority()
        before = len(calls(env))
        for changes in ({"tls": "ca", "certificate": other.public_bytes(serialization.Encoding.PEM).decode()},
                        {"tls": "system"}):
            with pytest.raises(HTTPException) as refused:
                await save(env, **rest_settings(env, endpoint=endpoint, **changes))
            assert refused.value.status_code == 400 and "has a certificate that restic refuses" in refused.value.detail
        assert len(calls(env)) == before
    # A self-signed certificate of the server's own, pasted as the CA, is a root too.
    certificate, key_pem = _certificate()
    (env.tmp / "own").mkdir()
    with tls_server(env.tmp / "own", certificate, key_pem) as port:
        server = env.state / "rest" / f"127.0.0.1_{port}"
        server.mkdir(parents=True)
        (server / "certificate.pem").write_text(certificate.decode())
        answer = await save(env, **rest_settings(env, endpoint=f"https://127.0.0.1:{port}", tls="ca",
                                                 certificate=certificate.decode()))
        assert answer["configured"]


async def test_a_pinned_certificate_that_restic_would_refuse_is_refused_with_why(env, monkeypatch):
    monkeypatch.setattr(offsite, "probe_store", REAL_PROBE_STORE, raising=False)
    authority, authority_key = _authority()
    expired, expired_key = _issued(authority, authority_key, days=-1)
    with tls_server(env.tmp, expired, expired_key) as port:
        with pytest.raises(HTTPException) as refused:
            await save(env, **rest_settings(env, endpoint=f"https://127.0.0.1:{port}", tls="fingerprint",
                                            certificate_sha256=_sha256(expired)))
    assert refused.value.status_code == 400 and "expired" in refused.value.detail and "nothing was saved" in (
        refused.value.detail)
    other_name, other_key = _certificate("127.0.0.2")
    (env.tmp / "named").mkdir()
    with tls_server(env.tmp / "named", other_name, other_key) as port:
        with pytest.raises(HTTPException) as refused:
            await save(env, **rest_settings(env, endpoint=f"https://127.0.0.1:{port}", tls="fingerprint",
                                            certificate_sha256=_sha256(other_name)))
    assert refused.value.status_code == 400 and "does not name 127.0.0.1" in refused.value.detail
    assert calls(env) == []


async def test_a_rest_server_that_does_not_answer_is_refused_at_once(env, monkeypatch):
    monkeypatch.setattr(offsite, "probe_store", REAL_PROBE_STORE, raising=False)
    with socket.create_server(("127.0.0.1", 0)) as closed:
        port = closed.getsockname()[1]
    endpoint = f"https://127.0.0.1:{port}"
    with pytest.raises(HTTPException) as refused:
        await save(env, **rest_settings(env, endpoint=endpoint))
    assert (refused.value.status_code, refused.value.detail) == (
        400, f"The REST server does not answer at {endpoint}: Connection refused")
    assert calls(env) == []


@pytest.mark.parametrize("changes, words", [
    ({"endpoint": "http://nas.example.com:8000"}, "https://"),
    ({"endpoint": "https://user:pw@nas.example.com"}, "scheme, the host and the port"),
    ({"endpoint": "https://nas.example.com/path"}, "scheme, the host and the port"),
    ({"prefix": "unos/../other"}, "folders of the copy repository"),
    ({"username": "with:colon"}, "user of the REST server"),
    ({"username": ""}, "user of the REST server"),
    ({"rest_password": None}, "password of the REST server"),
    ({"rest_password": "line\nbreak"}, "password of the REST server"),
    ({"tls": "none"}, "certificate"),
    ({"tls": "ca", "certificate": "not a certificate"}, "certificate"),
    ({"tls": "ca", "certificate": None}, "certificate"),
    ({"tls": "fingerprint", "certificate_sha256": "12:34"}, "fingerprint"),
])
async def test_rest_settings_that_restic_cannot_use_are_refused(env, changes, words):
    with pytest.raises(HTTPException) as refused:
        await save(env, **rest_settings(env, **changes))
    assert refused.value.status_code == 400 and words in refused.value.detail, refused.value.detail
    assert calls(env) == []


async def test_a_copy_its_check_and_restore_test_and_the_restores_run_to_a_rest_server(env, client):
    await save(env, **rest_settings(env))

    ok, message = await offsite.run_copy("task")

    assert ok, message
    assert commands(env)[2:] == ["unlock", "backup-stdin", "backup", "forget", "check", "dump"]
    assert all(call["env"]["RESTIC_REPOSITORY"] == f"rest:{REST}/unos/" for call in calls(env))
    snapshots = client.get(f"{API}/snapshots").json()["snapshots"]
    kinds = {snapshot["kind"]: snapshot for snapshot in snapshots}
    assert sorted(kinds) == ["configuration", "data"]
    restored = client.post(f"{API}/restore-configuration", json={"snapshot_id": kinds["configuration"]["id"]})
    assert restored.status_code == 200, restored.text
    files = client.post(f"{API}/restore-files", json={"snapshot_id": kinds["data"]["id"], "pool": "data"})
    assert files.status_code == 202, files.text
    status = wait_until_idle(client)
    assert status["last_restore"]["success"], status
    folder = Path(files.json()["folder"])
    assert (folder / str(env.pool / "docs" / "letter.txt").lstrip("/")).read_text() == "Dear family,\n"


# --- SFTP: the key pair ---------------------------------------------------------------------------


def test_unos_makes_the_key_pair_and_answers_only_its_public_key(env, client, caplog):
    caplog.set_level(logging.DEBUG)
    assert client.get(f"{API}/settings").json()["sftp_public_key"] is None

    answer = client.post(f"{API}/sftp-key")

    assert answer.status_code == 200, answer.text
    public = answer.json()["sftp_public_key"]
    assert re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/]+=* unos-off-device-copy", public), public
    secrets_file = env.data / "offsite_secrets.json"
    assert stat.S_IMODE(secrets_file.stat().st_mode) == 0o600
    private = json.loads(secrets_file.read_text())["sftp_private_key"]
    assert private.startswith("-----BEGIN OPENSSH PRIV" + "ATE KEY-----")
    assert fake_restic.public_key_of(private) == " ".join(public.split()[:2])
    body = private.split("\n")[1]
    assert body not in answer.text and body not in caplog.text
    assert not (env.data / "offsite.json").exists()
    assert client.get(f"{API}/settings").json()["sftp_public_key"] == public
    # A new key pair replaces it.
    again = client.post(f"{API}/sftp-key").json()["sftp_public_key"]
    assert again != public


async def test_sftp_needs_the_key_pair_of_unos_first(env):
    with pytest.raises(HTTPException) as refused:
        await save(env, **sftp_settings(env))
    assert refused.value.status_code == 400 and "key pair" in refused.value.detail
    assert calls(env) == []


async def test_a_host_without_ssh_refuses_an_sftp_target(env, key, monkeypatch):
    monkeypatch.setattr(host_layer, "missing_host_binaries", lambda names, **kwargs: ["ssh"])
    with pytest.raises(HTTPException) as refused:
        await save(env, **sftp_settings(env))
    assert refused.value.status_code == 400 and "openssh-client" in refused.value.detail
    assert calls(env) == []


# --- SFTP: the host key and the command -----------------------------------------------------------


async def test_the_first_test_pins_the_servers_host_key_and_every_later_command_holds_to_it(env, key, monkeypatch):
    monkeypatch.setattr(host_layer, "runs_locally", lambda: False)
    expected = server_key(env)

    answer = await save(env, **sftp_settings(env))

    assert answer["settings"]["host_key"] == expected
    assert answer["host_key_fingerprint"] == fingerprint(expected)
    assert commands(env) == ["cat", "init"]
    assert (sftp_server(env) / "files" / "srv" / "copies" / "unos" / "config").is_file()
    first, second = calls(env)
    assert first["env"]["RESTIC_REPOSITORY"] == "sftp://unos@backup.example.com:2222//srv/copies/unos"
    assert ssh_option(ssh_command(first), "StrictHostKeyChecking") == "accept-new"
    assert ssh_option(ssh_command(second), "StrictHostKeyChecking") == "yes"
    assert ssh_option(ssh_command(second), "HostKeyAlgorithms") == "ssh-ed25519"
    seen = ssh_log(env)
    assert seen[0]["known_hosts"] == "" and seen[1]["known_hosts"] == f"unos-copy-target {expected}\n"
    assert all(entry["identity_mode"] == 0o600 for entry in seen)

    ok, message = await offsite.run_copy("manual")
    assert ok, message
    for call in calls(env)[2:]:
        argv = ssh_command(call)
        assert ssh_option(argv, "StrictHostKeyChecking") == "yes"
    assert {entry["known_hosts"] for entry in ssh_log(env)[2:]} == {f"unos-copy-target {expected}\n"}


async def test_ssh_runs_with_a_fixed_argv_of_the_hosts_ssh_and_no_secret(env, key, monkeypatch):
    monkeypatch.setattr(host_layer, "runs_locally", lambda: False)
    await save(env, **sftp_settings(env))

    argv = ssh_command(calls(env)[-1])
    # The host's ssh, in the host's mount and network namespaces, without restic's environment.
    assert argv[:9] == ["nsenter", "--target", "1", "--mount", "--net", "--", "/usr/bin/env", "-i", "/usr/bin/ssh"]
    assert argv[9:11] == ["-F", "/dev/null"]
    assert argv[-4:] == ["-s", "--", "backup.example.com", "sftp"]
    assert (option(argv, "-p"), option(argv, "-l")) == (["2222"], ["unos"])
    for name, value in (("BatchMode", "yes"), ("PasswordAuthentication", "no"), ("KbdInteractiveAuthentication", "no"),
                        ("PreferredAuthentications", "publickey"), ("IdentitiesOnly", "yes"),
                        ("IdentityAgent", "none"), ("GlobalKnownHostsFile", "/dev/null"), ("ForwardAgent", "no"),
                        ("ClearAllForwardings", "yes"), ("PermitLocalCommand", "no"), ("UpdateHostKeys", "no")):
        assert ssh_option(argv, name) == value
    # The files of the container's TMPDIR, as the host's ssh opens them.
    pid = os.getpid()
    for name in ("IdentityFile", "UserKnownHostsFile"):
        path = ssh_option(argv, name)
        assert path.startswith(f"/proc/{pid}/root/"), path
        assert not os.path.exists(path.removeprefix(f"/proc/{pid}/root")), "the file outlived the command"
    # Each part a word that restic's splitting keeps whole: no space, quote or backslash.
    assert all(re.fullmatch(r"[A-Za-z0-9_./:=,@+-]+", part) for part in argv), argv
    private = json.loads((env.data / "offsite_secrets.json").read_text())["sftp_private_key"]
    for call in calls(env):
        assert PASSWORD not in " ".join(call["argv"]) and private.split("\n")[1] not in json.dumps(call)


async def test_a_changed_host_key_fails_the_runs_and_saves_until_a_save_trusts_it(env, key):
    pinned = server_key(env)
    await save(env, **sftp_settings(env))
    (sftp_server(env) / "host_key").write_text(fake_restic.ssh_key_line(b"a reinstalled server") + "\n")
    changed = server_key(env)

    ok, message = await offsite.run_copy("task")
    assert not ok and "Host key verification failed" in message, message
    assert json.loads((env.data / "offsite_status.json").read_text())["last_copy"]["step"] == "unlock"

    with pytest.raises(HTTPException) as refused:
        await save(env, **sftp_settings(env, password=None))
    assert refused.value.status_code == 400
    assert fingerprint(pinned) in refused.value.detail and "Trust" in refused.value.detail
    assert json.loads((env.data / "offsite.json").read_text())["host_key"] == pinned

    # Trusting a new key needs its fingerprint: a changed key is what a server in the middle shows.
    with pytest.raises(HTTPException) as refused:
        await save(env, **sftp_settings(env, password=None, trust_new_host_key=True))
    assert refused.value.status_code == 400 and "fingerprint of the server's new host key" in refused.value.detail
    with pytest.raises(HTTPException) as refused:
        await save(env, **sftp_settings(env, password=None, trust_new_host_key=True,
                                        host_key_fingerprint=fingerprint(pinned)))
    assert refused.value.status_code == 400 and fingerprint(changed) in refused.value.detail
    assert json.loads((env.data / "offsite.json").read_text())["host_key"] == pinned

    answer = await save(env, **sftp_settings(env, password=None, trust_new_host_key=True,
                                             host_key_fingerprint=fingerprint(changed)))
    assert answer["settings"]["host_key"] == changed
    assert answer["host_key_fingerprint"] == fingerprint(changed)
    ok, message = await offsite.run_copy("task")
    assert ok, message


async def test_a_fingerprint_given_while_a_pin_is_kept_is_compared_with_the_pin_before_restic_runs(env, key):
    pinned = server_key(env)
    await save(env, **sftp_settings(env))
    before = len(calls(env))
    other = fake_restic.ssh_key_line(b"another server")
    with pytest.raises(HTTPException) as refused:
        await save(env, **sftp_settings(env, password=None, host_key_fingerprint=fingerprint(other)))
    assert refused.value.status_code == 400
    assert f"pinned host key of the SFTP server at backup.example.com:2222 has the fingerprint {fingerprint(pinned)}" in (
        refused.value.detail) and "Trust" in refused.value.detail
    assert len(calls(env)) == before
    # The pin's own fingerprint passes.
    assert (await save(env, **sftp_settings(env, password=None, host_key_fingerprint=fingerprint(pinned))))["configured"]


async def test_a_server_that_drops_the_pinned_key_type_is_refused_with_the_trust_hint(env, key):
    await save(env, **sftp_settings(env))
    blob = base64.b64encode(fake_restic._ssh_string(b"ecdsa-sha2-nistp256") + fake_restic._ssh_string(b"x" * 65))
    (sftp_server(env) / "host_key").write_text(f"ecdsa-sha2-nistp256 {blob.decode()}\n")
    with pytest.raises(HTTPException) as refused:
        await save(env, **sftp_settings(env, password=None))
    assert refused.value.status_code == 400 and "no matching host key type found" in refused.value.detail
    assert "Trust" in refused.value.detail


async def test_another_server_is_pinned_anew_and_an_edit_of_the_same_keeps_the_pin(env, key):
    await save(env, **sftp_settings(env))
    before = len(calls(env))
    await save(env, **sftp_settings(env, password=None, check_percent=7))
    assert ssh_option(ssh_command(calls(env)[before]), "StrictHostKeyChecking") == "yes"

    other = server_key(env, port=22)
    answer = await save(env, **sftp_settings(env, port=22))
    assert answer["settings"]["host_key"] == other
    assert ssh_option(ssh_command(calls(env)[-2]), "StrictHostKeyChecking") == "accept-new"


async def test_a_fingerprint_typed_beforehand_is_checked_before_restic_makes_anything(env, key):
    expected = server_key(env)
    with pytest.raises(HTTPException) as refused:
        await save(env, **sftp_settings(env, host_key_fingerprint="SHA256:" + "A" * 43))
    assert refused.value.status_code == 400 and fingerprint(expected) in refused.value.detail
    assert commands(env) == ["cat"] and not (sftp_server(env) / "files").exists()
    assert not (env.data / "offsite.json").exists()

    answer = await save(env, **sftp_settings(env, host_key_fingerprint=fingerprint(expected)))
    assert answer["settings"]["host_key"] == expected


async def test_a_key_that_the_server_does_not_take_answers_400_and_saves_nothing(env, key):
    folder = sftp_server(env)
    folder.mkdir(parents=True)
    (folder / "authorized_keys").write_text(fake_restic.ssh_key_line(b"somebody else") + " other\n")
    with pytest.raises(HTTPException) as refused:
        await save(env, **sftp_settings(env))
    assert refused.value.status_code == 400 and "Permission denied (publickey)" in refused.value.detail
    assert not (env.data / "offsite.json").exists()

    (folder / "authorized_keys").write_text(key + "\n")
    answer = await save(env, **sftp_settings(env))
    assert answer["configured"]


@pytest.mark.parametrize("changes, words", [
    ({"host": "-oProxyCommand=touch"}, "host"),
    ({"host": "backup.example.com -oProxyCommand=x"}, "host"),
    ({"host": ""}, "host"),
    ({"port": 0}, "port"),
    ({"port": 65536}, "port"),
    ({"username": "-root"}, "user"),
    ({"username": "ro ot"}, "user"),
    ({"path": "~/copies"}, "folder on the SFTP server"),
    ({"path": "copies/../etc"}, "folder on the SFTP server"),
    ({"path": "my copies"}, "folder on the SFTP server"),
    ({"path": "a;b"}, "folder on the SFTP server"),
    ({"path": ""}, "folder on the SFTP server"),
    ({"host_key_fingerprint": "MD5:12"}, "fingerprint"),
])
async def test_sftp_settings_that_could_change_the_command_are_refused(env, key, changes, words):
    with pytest.raises(HTTPException) as refused:
        await save(env, **sftp_settings(env, **changes))
    assert refused.value.status_code == 400 and words in refused.value.detail, refused.value.detail
    assert calls(env) == []


async def test_a_host_key_that_a_request_names_is_not_taken(env, key):
    """The pin comes from the server at the first test, never from the request."""
    answer = await save(env, **sftp_settings(env, host_key=fake_restic.ssh_key_line(b"an attacker's")))
    assert answer["settings"]["host_key"] == server_key(env)


@pytest.mark.parametrize("field, value", [
    ("host", "backup.example.com -oProxyCommand=touch"),
    ("username", "unos -oLocalCommand=x"),
    ("path", "/srv/copies unos"),
    ("host_key", "ssh-ed25519 AAAA -oProxyCommand=x"),
    ("port", 70000),
])
async def test_stored_settings_edited_by_hand_are_refused_before_ssh_runs(env, key, field, value):
    await save(env, **sftp_settings(env))
    stored = json.loads((env.data / "offsite.json").read_text())
    stored[field] = value
    (env.data / "offsite.json").write_text(json.dumps(stored))
    before = len(calls(env))

    ok, message = await offsite.run_copy("task")

    assert not ok and len(calls(env)) == before, message
    assert json.loads((env.data / "offsite_status.json").read_text())["last_copy"]["step"] == "settings"


async def test_a_relative_folder_is_below_the_login_folder(env, key):
    answer = await save(env, **sftp_settings(env, path="copies/unos"))
    assert answer["settings"]["path"] == "copies/unos"
    assert calls(env)[0]["env"]["RESTIC_REPOSITORY"] == "sftp://unos@backup.example.com:2222/copies/unos"
    assert (sftp_server(env) / "files" / "home" / "unos" / "copies" / "unos" / "config").is_file()


async def test_an_ipv6_server_is_written_in_brackets_in_the_repository(env, key):
    await save(env, **sftp_settings(env, host="fd00::5"))
    assert calls(env)[0]["env"]["RESTIC_REPOSITORY"] == "sftp://unos@[fd00::5]:2222//srv/copies/unos"
    assert ssh_command(calls(env)[0])[-2] == "fd00::5"


async def test_a_copy_its_check_and_restore_test_and_the_restores_run_to_an_sftp_server(env, client, key):
    await save(env, **sftp_settings(env))

    ok, message = await offsite.run_copy("task")

    assert ok, message
    assert commands(env)[2:] == ["unlock", "backup-stdin", "backup", "forget", "check", "dump"]
    snapshots = client.get(f"{API}/snapshots").json()["snapshots"]
    kinds = {snapshot["kind"]: snapshot for snapshot in snapshots}
    assert sorted(kinds) == ["configuration", "data"]
    restored = client.post(f"{API}/restore-configuration", json={"snapshot_id": kinds["configuration"]["id"]})
    assert restored.status_code == 200, restored.text
    files = client.post(f"{API}/restore-files", json={"snapshot_id": kinds["data"]["id"], "pool": "data"})
    assert files.status_code == 202, files.text
    assert wait_until_idle(client)["last_restore"]["success"]
    # Every command after the first test, the listings and restores included, holds to the pin.
    later = calls(env)[2:]
    assert {command_of(call["argv"]) for call in later} >= {"snapshots", "dump", "restore"}
    assert {ssh_option(ssh_command(call), "StrictHostKeyChecking") for call in later} == {"yes"}


def test_the_settings_answer_names_the_pin_and_forget_removes_the_key_pair(env, client, key):
    saved = client.put(f"{API}/settings", json=sftp_settings(env))
    assert saved.status_code == 200, saved.text
    answer = client.get(f"{API}/settings").json()
    assert answer["host_key_fingerprint"] == fingerprint(server_key(env))
    assert answer["sftp_public_key"] == key and "sftp_private_key" not in json.dumps(answer)

    assert client.delete(f"{API}/settings").status_code == 200
    assert not (env.data / "offsite_secrets.json").exists()
    assert client.get(f"{API}/settings").json()["sftp_public_key"] is None


def test_the_key_pair_is_refused_while_a_copy_runs(env, client, key, monkeypatch):
    monkeypatch.setattr(offsite, "_activity", offsite.Activity("copy", offsite._utc_now()))
    answer = client.post(f"{API}/sftp-key")
    assert answer.status_code == 409 and "still running" in answer.json()["detail"]
