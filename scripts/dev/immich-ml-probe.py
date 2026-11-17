#!/usr/bin/env python3
"""Whether Immich's machine learning answers, and how fast: immich-ml-probe.py [--runs N] [--port P]
[--clip-model NAME] [--language CODE] [--text TEXT]

It runs inside Immich's machine-learning container, under the container's own python3, read from
standard input, so that nothing of it is written to a disk (ADR-0183):

    scripts/dev/ssh.sh 'docker exec -i immich-machine-learning python3 -I -B -' < scripts/dev/immich-ml-probe.py

python3 -I ignores the PYTHON* variables and the user's site folder, -B writes no bytecode, and
- reads the script from standard input; arguments follow the -. The probe keeps everything in
memory: it draws a picture of 1280x720 with two lines of text as a PNG (standard library only),
asks the machine learning at 127.0.0.1, or ::1, on port 3003 (IMMICH_PORT) whether it answers
/ping, and sends /predict the requests of Immich's server:

1. the picture, for CLIP's visual embedding, faces (buffalo_l's detection and recognition) and
   text (PP-OCRv5_mobile's detection and recognition), as the jobs Smart Search, Face Detection
   and OCR ask;
2. the text "a photo of a dog" (--text), for CLIP's textual embedding with the options of a
   search (its language, --language), as a smart search asks.

CLIP's model is Immich's default, ViT-B-32__openai, unless --clip-model names the one that
Immich's settings chose (Administration > Settings > Machine Learning > Smart Search): ask for the
model Immich uses, since the probe's first call downloads the model it names into /cache.

Each request is sent once, which loads its models and the first time downloads them into the
container's /cache, on the pool, and then N more times (--runs, 20). For each it prints the HTTP
status, the time of the first call, the median of the others, and the shape of the answer: its
keys, the lengths of its lists and embeddings and the text that OCR read in the picture, never
the numbers of an embedding. The probe reads no secret and prints none. Whether a model ran on
the NPU or on the CPU, the machine learning's log says ("Loaded RKNN model ..."), which goes,
like any container's log, to Docker's log on the host's system disk.

Exit status: 0 when every call answered 200, 1 when one did not, 2 for a usage error or when
the machine learning does not answer /ping. Standard library only; Python 3.9 or later.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib

# Immich's default models (its server's machine-learning settings) and the requests of its jobs.
CLIP_MODEL = "ViT-B-32__openai"
QUERY = "a photo of a dog"
# A model's name as Immich's catalog of models writes it, and a language code of a search.
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
LANGUAGE = re.compile(r"[A-Za-z]{2,3}([-_][A-Za-z0-9]{2,8})?")


def image_entries(clip_model: str) -> dict:
    return {
        "clip": {"visual": {"modelName": clip_model}},
        "facial-recognition": {"detection": {"modelName": "buffalo_l"}, "recognition": {"modelName": "buffalo_l"}},
        "ocr": {"detection": {"modelName": "PP-OCRv5_mobile"}, "recognition": {"modelName": "PP-OCRv5_mobile"}},
    }


def text_entries(clip_model: str, language: str | None) -> dict:
    """As Immich's server asks for a search's embedding: the model and the search's options."""
    options = {"language": language} if language else {}
    return {"clip": {"textual": {"modelName": clip_model, "options": options}}}

HOSTS = ("127.0.0.1", "[::1]")
WIDTH, HEIGHT, SCALE = 1280, 720, 8
LINES = ("IMMICH ON THE NPU", "UNOS PROBE 2026")
# A font of 5x7 dots for the letters of LINES.
FONT = {
    " ": ("00000",) * 7,
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
}


def picture() -> bytes:
    """A PNG of WIDTH x HEIGHT, RGB: dark text of LINES on a light background, in memory."""
    rows = [bytearray(bytes((236, 240, 244)) * WIDTH) for _ in range(HEIGHT)]
    ink = bytes((20, 24, 28)) * SCALE
    for number, line in enumerate(LINES):
        top = HEIGHT // 2 - 9 * SCALE + number * 11 * SCALE
        left = (WIDTH - len(line) * 6 * SCALE) // 2
        for dy in range(7):
            for index, char in enumerate(line):
                for dx, dot in enumerate(FONT[char][dy]):
                    if dot == "1":
                        x = 3 * (left + (index * 6 + dx) * SCALE)
                        for y in range(top + dy * SCALE, top + (dy + 1) * SCALE):
                            rows[y][x:x + len(ink)] = ink

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def form(entries: dict, image: bytes | None = None, text: str | None = None) -> tuple[bytes, str]:
    """A multipart/form-data body as Immich's server sends it to /predict, and its content type."""
    boundary = "unos-probe-" + os.urandom(12).hex()
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="entries"\r\n\r\n{json.dumps(entries)}\r\n'.encode()]
    if image is not None:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="probe.png"\r\n'
                     f'Content-Type: image/png\r\n\r\n'.encode() + image + b"\r\n")
    if text is not None:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="text"\r\n\r\n{text}\r\n'.encode())
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


# Straight to the machine learning: no proxy of the environment.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(url: str, body: bytes | None, content_type: str | None, timeout: float) -> tuple[int, float, bytes]:
    """(HTTP status, seconds, answer) of a request; status 0 when none came, with the reason."""
    request = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET")
    if content_type:
        request.add_header("Content-Type", content_type)
    start = time.monotonic()
    try:
        with OPENER.open(request, timeout=timeout) as answer:
            data = answer.read()
            return answer.status, time.monotonic() - start, data
    except urllib.error.HTTPError as e:
        data = e.read()
        return e.code, time.monotonic() - start, data
    except (OSError, ValueError) as e:  # refused, reset, timed out (URLError is an OSError)
        return 0, time.monotonic() - start, str(getattr(e, "reason", e)).encode()


def printable(text: str, limit: int = 200) -> str:
    """TEXT without control characters, cut at LIMIT."""
    clean = "".join(c if c.isprintable() else " " for c in text)
    return clean if len(clean) <= limit else clean[:limit] + "..."


def shape(value: object) -> str:
    """What VALUE is, without its numbers or its text: keys, types and lengths."""
    if isinstance(value, dict):
        return "{" + ", ".join(f"{json.dumps(str(k))}: {shape(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return f"list of {len(value)}" + (f" [{shape(value[0])}, ...]" if value else "")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = None
        if isinstance(parsed, list) and parsed and all(isinstance(n, (int, float)) for n in parsed):
            return f"an embedding of {len(parsed)} numbers"
        return f"text of {len(value)} characters"
    if value is None:
        return "null"
    return type(value).__name__


def read_text(answer: object) -> list[str]:
    """The texts that OCR read, from the answer's ocr.text."""
    texts = answer.get("ocr", {}).get("text", []) if isinstance(answer, dict) else []
    if not isinstance(texts, list):
        return []
    return [printable(t, 60) for t in texts[:10] if isinstance(t, str)]


def probe(base: str, name: str, entries: dict, runs: int, timeout: float, **inputs: object) -> bool:
    """Send one request once and then RUNS times; print what it answered. True when every call
    answered 200."""
    body, content_type = form(entries, **inputs)  # type: ignore[arg-type]
    print(f"{name}:", flush=True)
    status, seconds, answer = call(base + "/predict", body, content_type, timeout)
    if status != 200:
        print(f"  first call: {'no answer' if status == 0 else f'status {status}'} after {seconds:.2f} s: "
              f"{printable(answer.decode('utf-8', 'replace'))}", flush=True)
        return False
    print(f"  first call: status 200 in {seconds:.2f} s (it loads the models, and downloads them the first time)",
          flush=True)
    times, statuses = [], []
    for _ in range(runs):
        status, seconds, _answer = call(base + "/predict", body, content_type, timeout)
        statuses.append(status)
        times.append(seconds)
    failed = sorted({s for s in statuses if s != 200})
    if times:
        print(f"  {runs} more calls: median {statistics.median(times):.3f} s (fastest {min(times):.3f} s, slowest "
              f"{max(times):.3f} s), " + (f"answered {', '.join(str(s) for s in failed)}" if failed else "all status 200"),
              flush=True)
    try:
        parsed = json.loads(answer)
    except ValueError:
        print(f"  answer: not JSON, {len(answer)} bytes", flush=True)
        return False
    print(f"  answer: {shape(parsed)}", flush=True)
    texts = read_text(parsed)
    if "ocr" in entries:
        print(f"  OCR read: {', '.join(repr(t) for t in texts) or 'nothing'}", flush=True)
    return not failed


def model_name(value: str) -> str:
    if not MODEL_NAME.fullmatch(value) or ".." in value:
        raise argparse.ArgumentTypeError(f"not a model's name: {value!r}")
    return value


def language_code(value: str) -> str:
    if not LANGUAGE.fullmatch(value):
        raise argparse.ArgumentTypeError(f"not a language code: {value!r}")
    return value


def runs_count(value: str) -> int:
    if not value.isdigit():
        raise argparse.ArgumentTypeError(f"not a number of calls: {value!r}")
    return int(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="immich-ml-probe.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=runs_count, default=20, help="the calls of each request after the first (20)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("IMMICH_PORT", "3003")),
                        help="the machine learning's port (IMMICH_PORT, 3003)")
    parser.add_argument("--timeout", type=float, default=1800,
                        help="seconds a call may take; the first downloads the models (1800)")
    parser.add_argument("--clip-model", type=model_name, default=CLIP_MODEL,
                        help=f"CLIP's model, the one Immich's settings chose ({CLIP_MODEL})")
    parser.add_argument("--language", type=language_code, default=None,
                        help="the language of the text's search, as Immich's server sends it (none)")
    parser.add_argument("--text", default=QUERY, help=f"the text of the search ({QUERY!r})")
    args = parser.parse_args(argv)

    base, reasons = None, []
    for host in HOSTS:
        status, _seconds, answer = call(f"http://{host}:{args.port}/ping", None, None, 10)
        if status == 200 and answer.strip() == b"pong":
            base = f"http://{host}:{args.port}"
            break
        reasons.append(f"{host}: " + (f"status {status}" if status else printable(answer.decode("utf-8", "replace"))))
    if base is None:
        print(f"immich-ml-probe: the machine learning does not answer /ping on port {args.port} ({'; '.join(reasons)})",
              file=sys.stderr)
        return 2
    print(f"immich-ml-probe: {base} answers /ping", flush=True)
    image = picture()
    ok = probe(base, f"a picture of {WIDTH}x{HEIGHT} ({len(image)} bytes of PNG): CLIP visual, faces, OCR",
               image_entries(args.clip_model), args.runs, args.timeout, image=image)
    ok = probe(base, f"the text {args.text!r}: CLIP textual", text_entries(args.clip_model, args.language),
               args.runs, args.timeout, text=args.text) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
