#!/usr/bin/env python3
"""Run the supervisor's emergency command line against the local stand-in's data.

On a NAS, root runs `python -m src.emergency` in the supervisor's container through
deploy/reset-admin.sh (ADR-0284). This runs the same module beside the stand-in's supervisor,
with the paths of scripts/standin/run_supervisor.py: the database and the audit log of the
stand-in's state directory, never /data of this machine. webui/tests/emergency-reset.spec.ts
runs it; scripts/standin/e2e.sh gives the specs its Python and state directory
(E2E_STANDIN_PYTHON, E2E_STANDIN_STATE). supervisor/tests/test_emergency.py checks it.

Usage: emergency.py [--state-dir DIR] ARGUMENTS...
  ARGUMENTS  those of python -m src.emergency, such as: reset-admin admin
The new password is read as the command reads it: from standard input, or asked on a terminal.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import run_supervisor as standin


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--state-dir", type=Path, default=standin.DEFAULT_STATE, help="default: .standin/ in the repository")
    parser.add_argument("arguments", nargs=argparse.REMAINDER, help="the arguments of python -m src.emergency")
    args = parser.parse_args(argv)

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise SystemExit("emergency.py: refusing to run as root, like the stand-in's supervisor")
    state = args.state_dir.resolve()
    if not (state / standin.MARKER).is_file():
        raise SystemExit(f"emergency.py: {state} is not the state directory of a stand-in (no {standin.MARKER})")
    standin.configure(state)

    from src import emergency

    return emergency.main(args.arguments)


if __name__ == "__main__":
    sys.exit(main())
