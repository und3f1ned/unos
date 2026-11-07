"""Local servers that the notification senders deliver to in the tests: a mail
server and a webhook receiver on 127.0.0.1, so that smtplib, ssl and httpx
speak their real protocols (test_notifications.py)."""

import base64
import datetime
import http.server
import ipaddress
import socket
import ssl
import threading
from pathlib import Path
from types import SimpleNamespace


def certificate(directory: Path, *, dns: tuple[str, ...] = (), ips: tuple[str, ...] = ()) -> tuple[Path, Path]:
    """A self-signed certificate for the given names, as a test mail server would have.

    Returns the certificate file (also usable as the CA file that trusts it) and the key file.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, (dns or ips)[0])])
    now = datetime.datetime.now(datetime.timezone.utc)
    names = [x509.DNSName(n) for n in dns] + [x509.IPAddress(ipaddress.ip_address(a)) for a in ips]
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = directory / "server.crt", directory / "server.key"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_file, key_file


class MailServer:
    """An SMTP server on 127.0.0.1 with the given certificate.

    ``implicit_tls`` speaks TLS from the first byte (SMTPS, as on port 465); otherwise the
    connection starts in plain text and offers STARTTLS, and AUTH only after it. It records the
    logins (user, password, whether TLS protected them) and the messages it accepted
    (sender, recipients, text), and keeps a failed TLS handshake in ``errors``.
    """

    def __init__(self, cert_file: Path, key_file: Path, *, implicit_tls: bool = False):
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(cert_file, key_file)
        self.implicit_tls = implicit_tls
        self.logins: list[tuple[str, str, bool]] = []
        self.messages: list[tuple[str, list[str], str]] = []
        self.errors: list[Exception] = []
        self._listener = socket.create_server(("127.0.0.1", 0))
        self._listener.settimeout(0.1)
        self.port = self._listener.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> "MailServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(5)
        self._listener.close()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _ = self._listener.accept()
            except TimeoutError:
                continue
            connection.settimeout(5)
            try:
                self._converse(connection)
            except (OSError, ssl.SSLError) as error:
                self.errors.append(error)
            finally:
                connection.close()

    def _converse(self, connection: socket.socket) -> None:
        tls = self.implicit_tls
        if tls:
            connection = self.context.wrap_socket(connection, server_side=True)
        reader = connection.makefile("rb")

        def reply(*lines: str) -> None:
            text = "".join(f"250-{line}\r\n" for line in lines[:-1]) + f"{lines[-1]}\r\n"
            connection.sendall(text.encode())

        reply("220 mail.example ESMTP")
        mail_from, rcpt_to = "", []
        while True:
            line = reader.readline().decode().rstrip("\r\n")
            if not line:
                return
            verb, _, argument = line.partition(" ")
            verb = verb.upper()
            if verb == "EHLO":
                extension = "AUTH PLAIN" if tls else "STARTTLS"
                reply("mail.example", f"250 {extension}")
            elif verb == "STARTTLS" and not tls:
                reply("220 2.0.0 Ready to start TLS")
                connection = self.context.wrap_socket(connection, server_side=True)
                reader = connection.makefile("rb")
                tls = True
            elif verb == "AUTH" and argument.upper().startswith("PLAIN "):
                _, user, password = base64.b64decode(argument.split(" ", 1)[1]).decode().split("\0")
                self.logins.append((user, password, tls))
                reply("235 2.7.0 Authentication successful")
            elif verb == "MAIL":
                mail_from, rcpt_to = argument.split(":", 1)[1].strip("<>"), []
                reply("250 2.1.0 OK")
            elif verb == "RCPT":
                rcpt_to.append(argument.split(":", 1)[1].strip("<>"))
                reply("250 2.1.5 OK")
            elif verb == "DATA":
                reply("354 End data with <CR><LF>.<CR><LF>")
                lines = []
                while (data := reader.readline().decode()) not in (".\r\n", ""):
                    lines.append(data[1:] if data.startswith("..") else data)
                self.messages.append((mail_from, rcpt_to, "".join(lines)))
                reply("250 2.0.0 OK: queued")
            elif verb == "QUIT":
                reply("221 2.0.0 Bye")
                return
            else:
                reply("502 5.5.2 Command not recognized")


class HookServer:
    """A webhook receiver on 127.0.0.1 that records each request and answers ``answer``:
    (status, body, extra headers). ``url`` is its address."""

    def __init__(self, status: int = 200, body: bytes = b"", headers: dict[str, str] | None = None):
        self.answer = (status, body, headers or {})
        self.requests: list[SimpleNamespace] = []
        receiver = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _receive(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                receiver.requests.append(
                    SimpleNamespace(method=self.command, path=self.path, headers=self.headers, body=body)
                )
                status, answer, extra = receiver.answer
                self.send_response(status)
                for name, value in extra.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(answer)))
                self.end_headers()
                self.wfile.write(answer)

            do_POST = do_PUT = _receive

            def log_message(self, *args) -> None:
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "HookServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(5)
