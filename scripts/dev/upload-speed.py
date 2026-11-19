#!/usr/bin/env python3
"""How fast this workstation uploads to the dev device: scripts/dev/upload-speed.py [--mib N] [--runs R]

What a browser's upload goes through: Caddy over HTTPS, then uvicorn's HTTP parser and the
supervisor's upload route (POST /api/v1/files/upload), into a folder of the pool. It signs in
with UNOS_DEV_ADMIN_USER and UNOS_DEV_ADMIN_PASSWORD of dev.env (env.sh reads it, as for api.py);
the token stays in this process and in curl's standard input, never on a command line. It makes
the folder unoscheck<6 hex digits> at the root of the pool, writes a file of N MiB of random data
here (in TMPDIR), uploads it R times and prints each run's time and rate, deletes the folder for
good and signs out. The uploads go to the pool, not to the SD card: the sign-in and the sign-out
write about 0.1 MiB there (measure with scripts/dev/sd.sh). Exit status: 0 when every upload
answered 200.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import ssl
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api  # load_settings: dev.env through env.sh

POOL = "/mnt/pools/data"


def _request(base: str, method: str, path: str, token: str | None = None, body: object = None,
             params: dict | None = None) -> tuple[int, object]:
    url = base + path + ("?" + urllib.parse.urlencode(params) if params else "")
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)  # noqa: S310 - https://
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # the device's local CA
    try:
        with urllib.request.urlopen(request, context=context, timeout=60) as answer:  # noqa: S310 - https://
            text = answer.read().decode()
            return answer.status, json.loads(text) if text else None
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def _upload(base: str, token: str, folder: str, file: Path) -> tuple[int, float]:
    """One upload with curl; (HTTP status, seconds)."""
    config = (
        f'header = "Authorization: Bearer {token}"\n'
        f'url = "{base}/api/v1/files/upload?{urllib.parse.urlencode({"path": folder})}"\n'
    )
    command = ["curl", "-sk", "-K", "-", "-o", "/dev/null", "-w", "%{http_code} %{time_total}",
               "-F", f"file=@{file};type=application/octet-stream"]
    result = subprocess.run(command, input=config, capture_output=True, text=True)
    status, seconds = result.stdout.split()
    return int(status), float(seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mib", type=int, default=2048, help="the size of the uploaded file (MiB)")
    parser.add_argument("--runs", type=int, default=3, help="how many uploads")
    args = parser.parse_args()

    settings = api.load_settings()
    host = settings.get("UNOS_DEV_HOST")
    password = settings.get("UNOS_DEV_ADMIN_PASSWORD", "")
    if not host or not password:
        print("upload-speed: dev.env needs UNOS_DEV_HOST and UNOS_DEV_ADMIN_PASSWORD", file=sys.stderr)
        return 2
    base = f"https://{host}"
    status, answer = _request(base, "POST", "/api/v1/auth/login",
                              body={"username": settings.get("UNOS_DEV_ADMIN_USER") or "admin", "password": password})
    if status != 200 or not isinstance(answer, dict) or not answer.get("access_token"):
        print(f"upload-speed: the sign-in answered {status}", file=sys.stderr)
        return 1
    token = answer["access_token"]
    name = f"unoscheck{secrets.token_hex(3)}"
    folder = f"{POOL}/{name}"
    ok = True
    try:
        status, _ = _request(base, "POST", "/api/v1/files/mkdir", token, {"path": POOL, "name": name})
        if status != 200:
            print(f"upload-speed: making {folder} answered {status}", file=sys.stderr)
            return 1
        with tempfile.TemporaryDirectory() as scratch:
            file = Path(scratch) / "upload-speed.bin"
            with open(file, "wb") as out:
                for _ in range(args.mib):
                    out.write(os.urandom(1024 * 1024))
            for run in range(1, args.runs + 1):
                status, seconds = _upload(base, token, folder, file)
                ok = ok and status == 200
                rate = args.mib / seconds if seconds else 0
                print(f"run {run}: HTTP {status}, {args.mib} MiB in {seconds:.2f} s: "
                      f"{rate:.0f} MiB/s ({rate * 8 * 1.048576 / 1000:.2f} Gbit/s)")
                if status == 200:  # the next run replaces nothing: delete this one
                    _request(base, "DELETE", "/api/v1/files/delete", token, [f"{folder}/upload-speed.bin"],
                             {"permanent": "true"})
    finally:
        status, _ = _request(base, "DELETE", "/api/v1/files/delete", token, [folder], {"permanent": "true"})
        print(f"deleted {folder}: HTTP {status}")
        status, _ = _request(base, "POST", "/api/v1/auth/logout", token)
        print(f"signed out: HTTP {status}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
