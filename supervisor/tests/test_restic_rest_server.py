"""scripts/dev/restic-rest-server.py, the REST server of the device check of the off-device copy to a
REST server (backup.off-device-copies-over-sftp, ADR-0422): restic's REST protocol as its client
uses it, over HTTPS with basic authentication from a password file.

* restic's calls: POST /<repo>/?create=true, HEAD and GET of config and of a file (with a Range,
  206 and Content-Range), POST of a file, which is never replaced (403), the listings of a type in
  versions 1 and 2, DELETE, and 404 for what is not there;
* the files as restic's local back end lays them out, data in data/<2 hex digits>/<name>;
* 401 without the user and password, names and paths that would leave the root refused with 400.
"""

from __future__ import annotations

import base64
import datetime as dt
import http.client
import ipaddress
import json
import socket
import ssl
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

SERVER = Path(__file__).resolve().parents[2] / "scripts" / "dev" / "restic-rest-server.py"
NAME = "ab" + "0" * 62
PASSWORD = "the check's REST password"


def _certificate(folder: Path) -> tuple[Path, Path]:
    private = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = dt.datetime.now(dt.timezone.utc)
    certificate = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(private.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                       critical=False)
        .sign(private, hashes.SHA256())
    )
    cert, key = folder / "cert.pem", folder / "key.pem"
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    return cert, key


@pytest.fixture
def server(tmp_path):
    cert, key = _certificate(tmp_path)
    (tmp_path / "password").write_text(PASSWORD + "\n")
    root = tmp_path / "root"
    root.mkdir()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", str(SERVER), "--listen", f"127.0.0.1:{port}", "--root", str(root),
         "--cert", str(cert), "--key", str(key), "--user", "unoscheck", "--password-file", str(tmp_path / "password")],
        stderr=subprocess.PIPE, text=True,
    )
    try:
        assert "serves" in process.stderr.readline()
        context = ssl.create_default_context(cafile=str(cert))

        def request(method, path, body=None, headers=None, auth=True, length=True):
            """An iterable body goes chunked (http.client does so without a Content-Length);
            length=False sends no Content-Length and no body at all."""
            connection = http.client.HTTPSConnection("127.0.0.1", port, context=context, timeout=10)
            sent = dict(headers or {})
            if auth:
                sent["Authorization"] = "Basic " + base64.b64encode(f"unoscheck:{PASSWORD}".encode()).decode()
            if length:
                connection.request(method, path, body=body, headers=sent)
            else:
                connection.putrequest(method, path)
                for name, value in sent.items():
                    connection.putheader(name, value)
                connection.endheaders()
            answer = connection.getresponse()
            data = answer.read()
            connection.close()
            return answer, data

        yield request, root
    finally:
        process.terminate()
        process.wait(10)


def test_the_calls_of_restics_client_round_trip(server):
    request, root = server
    assert request("HEAD", "/unos/config")[0].status == 404
    assert request("POST", "/unos/?create=true", body=b"")[0].status == 200
    assert request("POST", "/unos/config", body=b"the config")[0].status == 200
    answer, _ = request("HEAD", "/unos/config")
    assert (answer.status, answer.getheader("Content-Length")) == (200, "10")
    assert request("GET", "/unos/config")[1] == b"the config"

    assert request("POST", f"/unos/data/{NAME}", body=b"0123456789")[0].status == 200
    assert (root / "unos" / "data" / "ab" / NAME).read_bytes() == b"0123456789"
    answer, data = request("GET", f"/unos/data/{NAME}", headers={"Range": "bytes=2-5"})
    assert (answer.status, data, answer.getheader("Content-Range")) == (206, b"2345", "bytes 2-5/10")
    answer, data = request("GET", f"/unos/data/{NAME}", headers={"Range": "bytes=7-"})
    assert (answer.status, data) == (206, b"789")
    # A file is never replaced.
    assert request("POST", f"/unos/data/{NAME}", body=b"other")[0].status == 403

    answer, data = request("GET", "/unos/data/", headers={"Accept": "application/vnd.x.restic.rest.v2"})
    assert answer.getheader("Content-Type") == "application/vnd.x.restic.rest.v2"
    assert json.loads(data) == [{"name": NAME, "size": 10}]
    assert json.loads(request("GET", "/unos/data/")[1]) == [NAME]
    assert json.loads(request("GET", "/unos/locks/")[1]) == []

    assert request("DELETE", f"/unos/data/{NAME}")[0].status == 200
    assert request("DELETE", f"/unos/data/{NAME}")[0].status == 404
    assert request("GET", f"/unos/keys/{NAME}")[0].status == 404


def test_restic_018s_bodies_without_a_length_are_read(server):
    """restic 0.18 sends POST ?create=true without a Content-Length and its uploads chunked: the
    batch-16 device check failed at its first save with 411 (RFC 9112 6.3: no Content-Length and no
    Transfer-Encoding is an empty body)."""
    request, root = server
    assert request("POST", "/unos/?create=true", body=None, length=False)[0].status == 200
    assert request("POST", "/unos/config", body=iter([b"the ", b"chunked ", b"config"]))[0].status == 200
    assert (root / "unos" / "config").read_bytes() == b"the chunked config"
    assert request("POST", f"/unos/data/{NAME}", body=iter([b"01234", b"56789"]))[0].status == 200
    assert (root / "unos" / "data" / "ab" / NAME).read_bytes() == b"0123456789"


def test_requests_without_the_password_and_paths_out_of_the_root_are_refused(server):
    request, root = server
    assert request("HEAD", "/unos/config", auth=False)[0].status == 401
    answer, _ = request("POST", "/unos/?create=true", body=b"", auth=False)
    assert answer.status == 401 and not (root / "unos").exists()
    assert request("GET", "/unos/data/..%2F..%2Fetc")[0].status == 400
    assert request("GET", "/../config")[0].status in (400, 404)
    assert request("POST", "/../evil/?create=true", body=b"")[0].status == 400
    assert not (root.parent / "evil").exists()
