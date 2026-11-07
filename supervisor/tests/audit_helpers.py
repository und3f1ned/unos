"""Reading the audit log in tests: one JSON entry per line (ADR-0224)."""
from __future__ import annotations

import json
from pathlib import Path


def audit_entries(path: Path) -> list[dict]:
    """The entries of one audit log file, oldest first ([] when it does not exist).

    Splits on b"\\n" only, as the server's reader does (an entry may hold U+2028 and the like),
    and fails on a damaged line: tests that read the log this way expect none.
    """
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_bytes().split(b"\n") if line.strip()]
