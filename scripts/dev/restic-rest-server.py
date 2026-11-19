#!/usr/bin/env python3
"""A minimal restic REST server over HTTPS, for the device check of the off-device copy to a REST
server (backup.off-device-copies-over-sftp, ADR-0422): the owner's second NAS is not needed.

  python3 -I -B restic-rest-server.py --listen ADDRESS:PORT --root FOLDER --cert CERT.pem
      --key KEY.pem --user NAME --password-file FILE

Copied into /dev/shm of the dev device and run there with the host's python3, in the background, it
serves restic's REST protocol (versions 1 and 2) at https://ADDRESS:PORT/<repository>/ for the
repositories below FOLDER (a folder of the pool, not the SD card), with basic authentication: NAME
and the password in FILE (mode 600, in RAM), never on a command line. One repository per path of
folder names; its files as restic's local back end lays them out (data/<2 hex digits>/<name>).
It writes nothing but the repositories' files and logs nothing but a line for a refused or failed
request, on standard error. A sketch of rest-server's API, not a replacement: no append-only mode,
no quotas, no metrics. Standard library only; Python 3.9 or later.
"""

from __future__ import annotations

import argparse
import base64
import hmac
import http.server
import json
import os
import re
import ssl
import sys
import tempfile

TYPES = ("data", "index", "keys", "locks", "snapshots")
V2 = "application/vnd.x.restic.rest.v2"
_NAME = re.compile(r"[0-9a-f]{64}")
_FOLDER = re.compile(r"[A-Za-z0-9._-]{1,128}")
_RANGE = re.compile(r"bytes=(\d+)-(\d*)")
CHUNK = 1024 * 1024


# The longest line of a chunked body's size or trailer that the server reads.
MAX_LINE = 1024

class Refused(Exception):
    def __init__(self, status: int, text: str) -> None:
        super().__init__(text)
        self.status = status


def parse(path: str) -> tuple[list[str], str, str | None]:
    """The repository's folders, what the request names (repository, config or a type) and the
    file's name: /repo/config, /repo/<type>/, /repo/<type>/<name>, /repo/."""
    parts = [p for p in path.split("?", 1)[0].split("/") if p]
    if parts and parts[-1] == "config":
        repo, what, name = parts[:-1], "config", None
    elif len(parts) >= 2 and parts[-2] in TYPES:
        repo, what, name = parts[:-2], parts[-2], parts[-1]
        if not _NAME.fullmatch(name):
            raise Refused(400, f"invalid name {name!r}")
    elif parts and parts[-1] in TYPES:
        repo, what, name = parts[:-1], parts[-1], None
    else:
        repo, what, name = parts, "repository", None
    if any(not _FOLDER.fullmatch(p) or p in (".", "..") or p in TYPES for p in repo):
        raise Refused(400, "invalid repository path")
    return repo, what, name


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "unoscheck-rest"
    root = ""
    credentials = b""

    def log_message(self, format: str, *args) -> None:  # noqa: A002  only refusals and failures, below
        pass

    def _answer(self, status: int, body: bytes = b"", content_type: str = "application/octet-stream",
                headers: dict | None = None, head: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _authorized(self) -> bool:
        given = self.headers.get("Authorization", "")
        if not given.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(given[6:], validate=True)
        except ValueError:
            return False
        return hmac.compare_digest(decoded, self.credentials)

    def _file(self, repo: list[str], what: str, name: str | None) -> str:
        folder = os.path.join(self.root, *repo)
        if what == "config":
            return os.path.join(folder, "config")
        if what == "data":
            return os.path.join(folder, "data", name[:2], name)
        return os.path.join(folder, what, name)

    def _body(self) -> bytes:
        """The request's body: chunked, of its Content-Length, or none. restic 0.18 sends POST
        ?create=true without a length and its uploads chunked; a request with neither header has
        an empty body (RFC 9112 6.3)."""
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            parts = []
            while True:
                line = self.rfile.readline(MAX_LINE)
                try:
                    size = int(line.split(b";")[0].strip(), 16)
                except ValueError:
                    raise Refused(400, "a chunk without its size") from None
                if size == 0:
                    while self.rfile.readline(MAX_LINE) not in (b"\r\n", b"\n", b""):
                        pass  # the trailer
                    return b"".join(parts)
                parts.append(self.rfile.read(size))
                self.rfile.readline(MAX_LINE)
        length = self.headers.get("Content-Length")
        if length is None:
            return b""
        if not length.isdigit():
            raise Refused(400, "a Content-Length that is not a number")
        return self.rfile.read(int(length))

    def _handle(self, method: str) -> None:
        try:
            if not self._authorized():
                if method in ("POST", "PUT"):
                    self._body()
                self._answer(401, b"401 Unauthorized\n", "text/plain", {"WWW-Authenticate": 'Basic realm="restic"'})
                sys.stderr.write(f"{method} {self.path}: 401\n")
                return
            repo, what, name = parse(self.path)
            getattr(self, f"_{method.lower()}")(repo, what, name)
        except Refused as e:
            self._answer(e.status, f"{e}\n".encode(), "text/plain", head=method == "HEAD")
            sys.stderr.write(f"{method} {self.path}: {e.status} {e}\n")
        except OSError as e:
            self._answer(500, b"500 Internal Server Error\n", "text/plain", head=method == "HEAD")
            sys.stderr.write(f"{method} {self.path}: 500 {e}\n")

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle("HEAD")

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def _head(self, repo, what, name) -> None:
        self._get(repo, what, name, head=True)

    def _get(self, repo, what, name, head: bool = False) -> None:
        if what == "repository":
            raise Refused(400, "nothing to read there")
        if what != "config" and name is None:
            return self._list(repo, what, head)
        path = self._file(repo, what, name)
        if not os.path.isfile(path):
            raise Refused(404, "not found")
        size = os.path.getsize(path)
        start, end, status = 0, size - 1, 200
        found = _RANGE.fullmatch(self.headers.get("Range", "")) if self.headers.get("Range") else None
        if self.headers.get("Range") and not found:
            raise Refused(416, "range not satisfiable")
        if found:
            start = int(found.group(1))
            end = min(int(found.group(2)), size - 1) if found.group(2) else size - 1
            if start >= size or end < start:
                raise Refused(416, "range not satisfiable")
            status = 206
        with open(path, "rb") as f:
            f.seek(start)
            body = b"" if head else f.read(end - start + 1)
        headers = {"Content-Range": f"bytes {start}-{end}/{size}"} if status == 206 else {}
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(end - start + 1))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _list(self, repo, what, head: bool) -> None:
        folder = os.path.join(self.root, *repo)
        if not os.path.isfile(os.path.join(folder, "config")) and not os.path.isdir(os.path.join(folder, what)):
            raise Refused(404, "no repository")
        found = []
        for current, _dirs, names in os.walk(os.path.join(folder, what)):
            for item in names:
                if _NAME.fullmatch(item):
                    found.append({"name": item, "size": os.path.getsize(os.path.join(current, item))})
        if V2 in self.headers.get("Accept", ""):
            self._answer(200, json.dumps(found).encode(), V2, head=head)
        else:
            self._answer(200, json.dumps([f["name"] for f in found]).encode(), "application/json", head=head)

    def _post(self, repo, what, name) -> None:
        if what == "repository":
            self._body()
            if "create=true" not in self.path.partition("?")[2].split("&"):
                raise Refused(400, "only ?create=true")
            for sub in TYPES:
                os.makedirs(os.path.join(self.root, *repo, sub), mode=0o700, exist_ok=True)
            return self._answer(200)
        if what != "config" and name is None:
            raise Refused(400, "a file's name is needed")
        body = self._body()
        path = self._file(repo, what, name)
        if os.path.exists(path):
            raise Refused(403, "the file exists")
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        fd, staged = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".staged-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(body)
                f.flush()
                os.fsync(f.fileno())
            os.link(staged, path)
        finally:
            os.unlink(staged)
        self._answer(200)

    def _delete(self, repo, what, name) -> None:
        if what == "repository" or name is None and what != "config":
            raise Refused(400, "a file's name is needed")
        path = self._file(repo, what, name)
        try:
            os.unlink(path)
        except FileNotFoundError:
            raise Refused(404, "not found") from None
        self._answer(200)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--listen", required=True, help="ADDRESS:PORT, such as 192.168.1.20:18443")
    parser.add_argument("--root", required=True, help="the folder of the repositories")
    parser.add_argument("--cert", required=True, help="the server's certificate, PEM")
    parser.add_argument("--key", required=True, help="its private key, PEM")
    parser.add_argument("--user", required=True, help="the user of basic authentication")
    parser.add_argument("--password-file", required=True, help="a file whose first line is the password")
    args = parser.parse_args(argv)
    address, _, port = args.listen.rpartition(":")
    with open(args.password_file) as f:
        password = f.readline().rstrip("\n")
    if not password or ":" in args.user:
        parser.error("an empty password, or a user with ':'")
    Handler.root = os.path.realpath(args.root)
    Handler.credentials = f"{args.user}:{password}".encode()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(args.cert, args.key)
    server = http.server.ThreadingHTTPServer((address.strip("[]"), int(port)), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    sys.stderr.write(f"restic-rest-server: https://{args.listen}/ serves {Handler.root}\n")
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
