#!/usr/bin/env python3
"""Call the UNOS API of the dev device.

  scripts/dev/api.py GET /system/stats
  scripts/dev/api.py GET /audit/logs --param limit=20
  echo '{"name": "x", "path": "/mnt/pools/data/x"}' | scripts/dev/api.py POST /shares/smb -
  scripts/dev/api.py --forget-token
  some-command | scripts/dev/api.py --redact     (a filter: text with the same redaction)
  echo '{"template_id": "x"}' | scripts/dev/api.py POST /apps/install -     (202: the install's job)
  scripts/dev/api.py --timeout 3660 GET /apps/install/jobs/<job id> --param wait=3600

PATH is relative to /api/v1. The request runs on the device itself, against
https://127.0.0.1/api/v1 (through Caddy, like a browser's): the device's web ports do not
answer the workstation's command-line tools. A short Python relay is started there through
scripts/dev/ssh.sh; the request, the administrator's login and the answer travel on SSH's
standard input and output, never on a command line (ADR-0212). The relay calls only the
device's own loopback address, and never through a proxy.

Login: UNOS_DEV_ADMIN_USER and UNOS_DEV_ADMIN_PASSWORD from dev.env. Every login commits a
row of the login history to the database and appends a line to the audit log, both on the
device's SD card (about 0.08 MiB in all, 0.15 MiB for the first after a start; ADR-0308),
and counts
against the login rate limit (5 per minute), so the relay keeps the token in the device's RAM
(/dev/shm/unos-dev-api.token, mode 600, root's; root there can mint tokens anyway; ADR-0222)
and signs in again only when the API refuses it. A cache that is not a regular file of root
with mode 600 (a symlink, a FIFO, a file another user put in the shared /dev/shm) is never
read: the relay signs in and replaces it, through a new file renamed over it. --forget-token
deletes it. --no-login sends the request without a token. --timeout SECONDS is how long the
relay waits for the API's answer (150 s by default; ssh gets 60 s more): an app's install
answers at once with its job (ADR-0383), and GET /apps/install/jobs/<id> with the parameter wait
answers once the job has ended (succeeded with its result, or failed with its error) or the
seconds have passed, so its --timeout must exceed the wait.

Output: "HTTP <status>", then the body (JSON pretty-printed) with secrets redacted: values of
keys that name a password, token, secret, key, webhook URL or recovery codes; KEY=value text
with such a KEY; the user part of URLs (user:password@, token@); JWTs; and the values of
dev.env's secrets wherever they appear. Exit status: 0 for a 2xx answer, 1 for another HTTP
status, 2 for a usage error or when the device could not be asked.

Importable: DeviceApi().get("/system/stats").json(); also post, put, patch, delete, request, and
upload, which sends a file as the form field "file" (multipart/form-data, as the web UI's uploads
do; the relay gets its bytes in base64 on standard input); as_user gives the same API signed in
as another user, with a token cache of its own (the scenario checks' temporary user).
Standard library only; Python 3.9 or later.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import secrets
import shlex
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SSH = HERE / "ssh.sh"
ENV_SH = HERE / "env.sh"
API_URL = "https://127.0.0.1/api/v1"
TOKEN_CACHE = "/dev/shm/unos-dev-api.token"
REDACTED = "<redacted>"
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

# The names of dev.env's secrets: the rule of dev__secret_name in env.sh.
SECRET_NAME_PARTS = ("PASS", "TOKEN", "SECRET", "API_KEY", "PRIVATE_KEY")
# JSON keys whose values are secrets (a Discord or Slack webhook URL holds its token in the
# path; recovery codes come as "codes") ...
SECRET_KEY = re.compile(
    r"pass(word|wd|phrase)?|secret|token|api[-_]?key|private[-_]?key|credential|authorization"
    r"|cookie|totp|otp[-_]?(uri|url)|recovery[-_]?codes?|webhook[-_]?url|(^|[-_])codes$",
    re.IGNORECASE,
)
# ... except keys about one (when it changed, whether it is set, its type).
NOT_SECRET_KEY = re.compile(
    r"(_at|_type|_enabled|_required|_set|_changed|_length|_count|_expires|_expires_in|_age|_hint|_policy)$",
    re.IGNORECASE,
)
JWT = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
# The user part of a URL, between "://" and "@": a user and a password, or a token.
URL_USERINFO = re.compile(r"(?<=://)[^\s/?#@\"'<>]+@")
# KEY=value or key: value in text, where KEY ends in a secret's word, as TRANSMISSION_PASS does.
TEXT_PAIR = re.compile(
    r"""(\b[\w-]*?(?:pass(?:word|wd|phrase)?|secret|token|api[_-]?key|private[_-]?key|webhook[_-]?url)\b)"""
    r"""(\s*["']?\s*[:=]\s*["']?)([^\s"'&,;]+)""",
    re.IGNORECASE,
)

# Runs on the device (its python3, standard library). Reads the request as JSON on standard
# input and writes {"status", "body"} or {"error"} as JSON on standard output. The request
# names the API ("base", only a loopback address) and the token cache ("cache").
RELAY = r'''
import base64, json, os, ssl, stat, sys, tempfile, urllib.error, urllib.parse, urllib.request
req = json.load(sys.stdin)
cache = req["cache"]
base = req.get("base") or "https://127.0.0.1/api/v1"

def done(**out):
    sys.stdout.write(json.dumps(out))
    sys.exit(0)

where = urllib.parse.urlsplit(base)
if where.scheme not in ("https", "http") or where.hostname not in ("127.0.0.1", "::1", "localhost"):
    done(error="the relay calls only the API of the device itself (127.0.0.1), not %s" % base)

if req.get("forget"):
    try:
        os.unlink(cache)
    except FileNotFoundError:
        pass
    except OSError as e:
        done(error="could not delete the kept token %s: %s" % (cache, e))
    done(status=200, body="{}")

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
# No proxy, whatever the environment says: the login and the token stay on the device.
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx))

# A body that is not JSON (an upload's form) comes in base64, with its content type.
raw = base64.b64decode(req["data"]) if req.get("data") is not None else None

def call(method, path, params=None, body=None, has_body=False, token=None, data=None, content_type=None):
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    if data is None and has_body:
        data = json.dumps(body).encode()
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = content_type or "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with opener.open(r, timeout=req.get("timeout", 150)) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        done(error="the API on the device did not answer: %s" % e)

# The kept token counts only from a regular file of this user (root on the device) with mode
# 600. Anything else at that path is never read: a symlink, a FIFO (O_NONBLOCK: no hang), a
# file that another user put in the shared /dev/shm, a file others can read. The next login
# replaces it.
def cached():
    try:
        fd = os.open(cache, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o600:
            return None
        return os.read(fd, 65536).decode("utf-8", "replace").strip() or None
    except OSError:
        return None
    finally:
        os.close(fd)

# A new file of mode 600 in the cache's directory, renamed over the cache: nothing is written
# through a link, and a reader never sees half a token.
def keep(token):
    folder, name = os.path.split(cache)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=folder or ".", prefix=name + ".")
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(token)
        os.replace(tmp, cache)
    except OSError as e:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        done(error="could not keep the token in %s: %s" % (cache, e))

def login():
    cred = req.get("login")
    if not cred:
        done(error="the API needs a login and UNOS_DEV_ADMIN_PASSWORD is empty in dev.env")
    status, body = call("POST", "/auth/login", body=cred, has_body=True)
    if status != 200:
        done(error="login as %s failed: HTTP %s" % (cred["username"], status))
    try:
        data = json.loads(body)
    except ValueError:
        done(error="the login answer is not JSON")
    if data.get("requires_totp"):
        done(error="the administrator has two-factor login on: api.py cannot sign in")
    token = data.get("access_token")
    if not token:
        done(error="the login answer has no token")
    keep(token)
    return token

token, fresh = None, False
if req.get("auth"):
    token = cached()
    if token is None:
        token, fresh = login(), True
args = (req["method"], req["path"], req.get("params"), req.get("body"), req.get("has_body", False))
extra = {"data": raw, "content_type": req.get("content_type")}
status, body = call(*args, token=token, **extra)
if status == 401 and req.get("auth") and not fresh:
    status, body = call(*args, token=login(), **extra)
done(status=status, body=body)
'''


# The relay runs with the device's root filesystem read-only and noatime in a private mount
# namespace (DEV_RO_ROOT of env.sh, ADR-0221): nothing it does writes to the SD card; the token
# cache is in /dev/shm, which stays writable.
RELAY_COMMAND = "unshare --mount --propagation private sh -c " + shlex.quote(
    'mount -o remount,bind,ro,noatime / && exec python3 -I -B -c "$1"'
) + " sh " + shlex.quote(RELAY)


class ApiError(Exception):
    """The device could not be asked (ssh, relay, login), as opposed to an HTTP error answer."""


class Answer:
    """The parts of an HTTP answer that callers use (named as in httpx and requests)."""

    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text

    def json(self) -> Any:
        return json.loads(self.text)

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


def is_secret_name(name: str) -> bool:
    upper = name.upper()
    return any(part in upper for part in SECRET_NAME_PARTS)


def is_secret_key(key: str) -> bool:
    return bool(SECRET_KEY.search(key)) and not NOT_SECRET_KEY.search(key)


def secret_values(settings: dict[str, str]) -> list[str]:
    """dev.env's secret values worth scrubbing (4 characters or more), longest first."""
    values = {value for name, value in settings.items() if is_secret_name(name) and len(value) >= 4}
    return sorted(values, key=len, reverse=True)


def redact_text(text: str, secrets: Iterable[str] = ()) -> str:
    for value in secrets:
        text = text.replace(value, REDACTED)
    text = JWT.sub(REDACTED, text)
    text = URL_USERINFO.sub(REDACTED + "@", text)
    return TEXT_PAIR.sub(lambda m: m.group(1) + m.group(2) + REDACTED, text)


def redact(value: Any, secrets: Iterable[str] = (), force: bool = False) -> Any:
    """A copy of a JSON value with secrets replaced by "<redacted>". force: every string."""
    secrets = tuple(secrets)
    if isinstance(value, dict):
        return {
            key: redact(item, secrets, force or (isinstance(key, str) and is_secret_key(key)))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, secrets, force) for item in value]
    if isinstance(value, str):
        if force and value:
            return REDACTED
        return redact_text(value, secrets)
    return value


def render(answer: Answer, secrets: Iterable[str] = (), raw: bool = False) -> str:
    """The CLI's output for an answer: the status line and the redacted body."""
    secrets = tuple(secrets)
    try:
        data = json.loads(answer.text)
    except ValueError:
        body = redact_text(answer.text, secrets)
    else:
        data = redact(data, secrets)
        body = json.dumps(data, ensure_ascii=False) if raw else json.dumps(data, indent=2, ensure_ascii=False)
    return f"HTTP {answer.status_code}\n{body}"


def load_settings() -> dict[str, str]:
    """dev.env's settings, read by env.sh (its checks, its parser) into a pipe."""
    script = '. "$1" || exit 1; dev__read --nul || exit 1; printf "UNOS_DEV_ADMIN_USER=%s\\0" "$UNOS_DEV_ADMIN_USER"'
    result = subprocess.run(["bash", "-c", script, "api.py", str(ENV_SH)], capture_output=True)
    if result.returncode != 0:
        raise ApiError(result.stderr.decode("utf-8", "replace").strip() or "could not read dev.env")
    settings: dict[str, str] = {}
    for item in result.stdout.split(b"\0"):
        if item:
            name, _, value = item.decode("utf-8", "surrogateescape").partition("=")
            settings[name] = value
    return settings


def api_path(path: str) -> str:
    if path.startswith("/api/v1/"):
        path = path[len("/api/v1"):]
    if not path.startswith("/") or "?" in path or "#" in path or re.search(r"\s|/\.\.?(/|$)", path):
        raise ValueError(f"PATH must look like /system/info (relative to /api/v1, no '?': use --param): {path!r}")
    return path


class DeviceApi:
    """The UNOS API of the dev device, called through the relay (see the module docstring).
    ssh runs the relay's command line (ssh.sh); base_url and token_cache are the relay's API
    and token file on the device (the tests run the relay locally with others)."""

    def __init__(
        self,
        settings: dict[str, str] | None = None,
        timeout: int = 150,
        ssh: Path = SSH,
        base_url: str = API_URL,
        token_cache: str = TOKEN_CACHE,
    ) -> None:
        self.settings = load_settings() if settings is None else settings
        self.secrets = secret_values(self.settings)
        self.timeout = timeout
        self.ssh = ssh
        self.base_url = base_url
        self.token_cache = token_cache

    def _relay(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = {**payload, "base": self.base_url, "cache": self.token_cache, "timeout": self.timeout}
        try:
            result = subprocess.run(
                [str(self.ssh), RELAY_COMMAND],
                input=json.dumps(payload).encode(),
                capture_output=True,
                timeout=self.timeout + 60,
            )
        except subprocess.TimeoutExpired as e:
            raise ApiError(f"no answer from the device within {e.timeout} s") from None
        if result.returncode != 0:
            err = redact_text(result.stderr.decode("utf-8", "replace"), self.secrets).strip()
            raise ApiError(f"the relay on the device failed (ssh status {result.returncode}): {err[-400:]}")
        try:
            answer = json.loads(result.stdout)
        except ValueError:
            raise ApiError("the relay's answer is not JSON") from None
        if "error" in answer:
            raise ApiError(redact_text(str(answer["error"]), self.secrets))
        return answer

    def request(
        self,
        method: str,
        path: str,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        auth: bool = True,
        data: bytes | None = None,
        content_type: str | None = None,
    ) -> Answer:
        """data and content_type: a body that is not JSON (it replaces json_body)."""
        payload: dict[str, Any] = {
            "method": method.upper(),
            "path": api_path(path),
            "params": params or None,
            "has_body": json_body is not None,
            "body": json_body,
            "auth": auth,
        }
        if data is not None:
            payload["data"] = base64.b64encode(data).decode()
            payload["content_type"] = content_type
        password = self.settings.get("UNOS_DEV_ADMIN_PASSWORD", "")
        if auth and password:
            payload["login"] = {"username": self.settings.get("UNOS_DEV_ADMIN_USER") or "admin", "password": password}
        answer = self._relay(payload)
        return Answer(int(answer["status"]), str(answer["body"]))

    def get(self, path: str, params: dict[str, Any] | None = None) -> Answer:
        return self.request("GET", path, params=params)

    def post(self, path: str, json: Any = None) -> Answer:  # json: the name httpx uses
        return self.request("POST", path, json_body=json)

    def put(self, path: str, json: Any = None) -> Answer:
        return self.request("PUT", path, json_body=json)

    def patch(self, path: str, json: Any = None) -> Answer:
        return self.request("PATCH", path, json_body=json)

    def delete(self, path: str, params: dict[str, Any] | None = None) -> Answer:
        return self.request("DELETE", path, params=params)

    def upload(self, path: str, params: dict[str, Any] | None, filename: str, content: bytes) -> Answer:
        """POST the file CONTENT, named FILENAME, as the form field "file" (multipart/form-data)."""
        if not re.fullmatch(r"[^\x00-\x1f\"\\/]+", filename):
            raise ValueError(f"not a plain file name: {filename!r}")
        boundary = "unos-" + secrets.token_hex(16)
        head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
                "Content-Type: application/octet-stream\r\n\r\n")
        body = head.encode() + content + f"\r\n--{boundary}--\r\n".encode()
        return self.request("POST", path, params=params, data=body,
                            content_type=f"multipart/form-data; boundary={boundary}")

    def forget_token(self) -> None:
        self._relay({"forget": True})

    def as_user(self, username: str, password: str, token_cache: str) -> DeviceApi:
        """The same API, signed in as USERNAME with PASSWORD (a user of UNOS without two-factor
        login), whose token the relay keeps in TOKEN_CACHE in the device's RAM, apart from the
        administrator's; forget_token deletes it. dev.env's secrets stay redacted."""
        other = DeviceApi(settings={"UNOS_DEV_ADMIN_USER": username, "UNOS_DEV_ADMIN_PASSWORD": password},
                          timeout=self.timeout, ssh=self.ssh, base_url=self.base_url, token_cache=token_cache)
        other.secrets = sorted(set(self.secrets) | set(other.secrets), key=len, reverse=True)
        return other

    def __enter__(self) -> DeviceApi:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def parse_params(items: list[str]) -> dict[str, list[str]]:
    params: dict[str, list[str]] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise ValueError(f"--param takes KEY=VALUE, not {item!r}")
        params.setdefault(key, []).append(value)
    return params


def positive_seconds(value: str) -> int:
    """--timeout's value: a whole number of seconds, 1 or more."""
    try:
        seconds = int(value)
    except ValueError:
        seconds = 0
    if seconds < 1:
        raise argparse.ArgumentTypeError(f"not a number of seconds, 1 or more: {value!r}")
    return seconds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="api.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("method", nargs="?", type=str.upper, choices=METHODS)
    parser.add_argument("path", nargs="?")
    parser.add_argument("body", nargs="?", choices=["-"], help="read a JSON body from standard input")
    parser.add_argument("--param", action="append", default=[], metavar="KEY=VALUE", help="a query parameter")
    parser.add_argument("--no-login", action="store_true", help="send the request without a token")
    parser.add_argument("--raw", action="store_true", help="print the JSON body on one line")
    parser.add_argument("--forget-token", action="store_true", help="delete the token kept on the device")
    parser.add_argument("--redact", action="store_true", help="copy standard input to standard output, redacted")
    parser.add_argument("--timeout", type=positive_seconds, default=150, metavar="SECONDS",
                        help="how long to wait for the answer (default 150)")
    args = parser.parse_args(argv)
    try:
        if args.redact:
            if args.method or args.path or args.forget_token:
                parser.error("--redact takes no other argument")
            secrets = secret_values(load_settings())
            for line in sys.stdin:
                sys.stdout.write(redact_text(line, secrets))
                sys.stdout.flush()
            return 0
        if args.forget_token:
            if args.method or args.path:
                parser.error("--forget-token takes no METHOD or PATH")
            DeviceApi().forget_token()
            print("token deleted on the device")
            return 0
        if not args.method or not args.path:
            parser.error("METHOD and PATH are required")
        params = parse_params(args.param)
        api_path(args.path)
        body = None
        if args.body == "-":
            try:
                body = json.load(sys.stdin)
            except ValueError as e:
                parser.error(f"standard input is not JSON: {e}")
        api = DeviceApi(timeout=args.timeout)
        answer = api.request(args.method, args.path, json_body=body, params=params, auth=not args.no_login)
    except (ApiError, ValueError) as e:
        print(f"api.py: {e}", file=sys.stderr)
        return 2
    print(render(answer, api.secrets, raw=args.raw))
    return 0 if answer.ok else 1


if __name__ == "__main__":
    sys.exit(main())
