"""The emergency command line (ADR-0284): root on the host resets an administrator's password and
turns its two-factor authentication off, when no administrator can sign in (docs/RECOVERY.md).

    sudo /opt/unos/deploy/reset-admin.sh [NAME]
    sudo docker exec -it unos-supervisor python -m src.emergency reset-admin [NAME]

reset-admin takes the administrator NAME, or the only administrator. The new password is asked
twice on a terminal, without echo; otherwise it is the first line of standard input. It never
comes from the command line or the environment, which other processes of the host can read
(ADR-0125). One UPDATE stores its hash and turns two-factor authentication off, only while the
account is still as it was read; then the audit log records the change, by the user root.

No route of the API reaches this module: it runs only in a process of its own, in the
supervisor's container, and no other module imports it.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
import unicodedata
from dataclasses import dataclass
from typing import TextIO
from urllib.parse import quote

from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from .audit.router import log_action
from .audit.schemas import AuditAction, AuditSeverity
from .auth.models import User, UserSession
from .auth.schemas import MAX_PASSWORD_BYTES
from .auth.utils import hash_password
from .config import get_settings
from .database import BUSY_TIMEOUT_SECONDS

# What the web UI asks of a new password (webui/src/routes/users/+page.svelte, the setup page).
MIN_PASSWORD_CHARS = 8
# The user of the audit entries: whoever runs the command is root on the host. No UNOS user can
# have the name (ADR-0115).
AUDIT_USER = "root"
NOTHING_CHANGED = "Nothing was changed."
PASSWORD_DETAILS = "Password reset on the host's command line"
TOTP_DETAILS = "Two-factor authentication turned off on the host's command line, with its recovery codes"


class EmergencyError(Exception):
    """The reset did not happen; the message says why."""

    exit_status = 1


class Interrupted(EmergencyError):
    """Ctrl+C at the password prompt."""

    exit_status = 130


@dataclass(frozen=True)
class Administrator:
    """An administrator's row as it was read: the reset changes it only while it is still so."""

    id: int
    username: str
    totp_enabled: bool | None


def _shown(name: str) -> str:
    """A name as the terminal gets it: control and format characters become '?'. Names come
    from the command line and from the database, which may be a restored, untrusted one
    (ADR-0144)."""
    return "".join("?" if unicodedata.category(c).startswith("C") else c for c in name)


def password_problem(password: str) -> str | None:
    """Why ``password`` cannot be the new password, or None."""
    if len(password) < MIN_PASSWORD_CHARS:
        return f"The new password must have at least {MIN_PASSWORD_CHARS} characters"
    try:
        size = len(password.encode("utf-8"))
    except UnicodeEncodeError:  # bytes that were not UTF-8, read with surrogateescape
        return "The new password must be UTF-8 text"
    if size > MAX_PASSWORD_BYTES:
        return f"The new password must not be longer than {MAX_PASSWORD_BYTES} bytes (UTF-8)"
    if any(unicodedata.category(c) == "Cc" for c in password):
        return "The new password must not contain control characters such as tabs"
    return None


def _engine(db_path: str) -> Engine:
    """The UNOS database at ``db_path``. mode=rw: a database that does not exist is an error,
    never a new empty file on the system disk."""
    return create_engine(
        f"sqlite:///file:{quote(db_path)}?mode=rw&uri=true",
        connect_args={"timeout": BUSY_TIMEOUT_SECONDS},
        poolclass=NullPool,
    )


def _reason(exc: SQLAlchemyError) -> str:
    """SQLite's own message (unable to open database file, database is locked), without the SQL."""
    return str(getattr(exc, "orig", None) or exc)


def _administrators(engine: Engine) -> list[Administrator]:
    with Session(engine) as session:
        rows = session.execute(
            select(User.id, User.username, User.totp_enabled)
            .where(User.is_admin.is_(True))
            .order_by(User.username)
        ).all()
    return [Administrator(*row) for row in rows]


def _choose(administrators: list[Administrator], name: str | None) -> Administrator:
    """The administrator ``name``, or the only administrator when ``name`` is None."""
    names = ", ".join(_shown(admin.username) for admin in administrators)
    if not administrators:
        raise EmergencyError(
            "UNOS has no administrator: while it has no account at all, the web interface's "
            "first-run setup creates one."
        )
    if name is None:
        if len(administrators) > 1:
            raise EmergencyError(f"UNOS has {len(administrators)} administrators ({names}): name the one to reset.")
        return administrators[0]
    for admin in administrators:
        if admin.username == name:
            return admin
    raise EmergencyError(f"'{_shown(name)}' is not an administrator of UNOS (administrators: {names}).")


def _read_password(stdin: TextIO | None, name: str) -> str:
    """The new password: asked twice on a terminal, else the first line of standard input."""
    if stdin is not None and stdin.isatty():
        try:
            password = getpass.getpass(f"New password for administrator '{_shown(name)}': ")
            if getpass.getpass("Repeat the new password: ") != password:
                raise EmergencyError("The two entries differ.")
        except KeyboardInterrupt:
            raise Interrupted("Interrupted.") from None
        except EOFError:
            password = ""
    else:
        try:
            password = stdin.readline() if stdin is not None else ""
        except UnicodeDecodeError:
            raise EmergencyError("The new password must be UTF-8 text.") from None
        password = password.rstrip("\r\n")
    if not password:
        raise EmergencyError(
            "No new password: type it on a terminal, or give it as the first line of standard input."
        )
    return password


def _store(engine: Engine, admin: Administrator, hashed: str) -> None:
    """Store the new hash and turn two-factor authentication off, as turning it off does
    (ADR-0238), in one UPDATE that changes the row only while it is as it was read; the
    administrator's sessions end in the same commit (ADR-0329)."""
    with Session(engine) as session:
        result = session.execute(
            update(User)
            .where(
                User.id == admin.id,
                User.username == admin.username,
                User.is_admin.is_(True),
                User.totp_enabled.is_(admin.totp_enabled),
            )
            .values(hashed_password=hashed, totp_enabled=False, totp_secret=None, recovery_codes=None)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            session.rollback()
            raise EmergencyError(
                f"Administrator '{_shown(admin.username)}' changed while the new password was typed: "
                "run the command again."
            )
        session.execute(delete(UserSession).where(UserSession.user_id == admin.id))
        session.commit()


async def _record(admin: Administrator) -> list[str]:
    """The audit entries of the reset; what kept any of them from being written."""
    entries = [(AuditAction.PASSWORD_CHANGE, PASSWORD_DETAILS)]
    if admin.totp_enabled:
        entries.append((AuditAction.TOTP_DISABLED, TOTP_DETAILS))
    problems = []
    for action, details in entries:
        try:
            await log_action(
                action,
                username=AUDIT_USER,
                resource_type="user",
                resource_id=admin.username,
                details=details,
                severity=AuditSeverity.WARNING,
            )
        except OSError as exc:  # log_action has reported the entry in the log
            problems.append(f"{action.value}: {exc.strerror or exc}")
    return problems


def reset_admin(name: str | None) -> int:
    """The command reset-admin; its exit status. Raises EmergencyError before anything changed."""
    db_path = get_settings().db_path
    engine = _engine(db_path)
    try:
        try:
            admin = _choose(_administrators(engine), name)
        except SQLAlchemyError as exc:
            raise EmergencyError(f"Could not read the UNOS database {db_path}: {_reason(exc)}.") from None
        password = _read_password(sys.stdin, admin.username)
        problem = password_problem(password)
        if problem:
            raise EmergencyError(f"{problem}.")
        hashed = hash_password(password)
        try:
            _store(engine, admin, hashed)
        except SQLAlchemyError as exc:
            raise EmergencyError(f"Could not write the UNOS database {db_path}: {_reason(exc)}.") from None
    finally:
        engine.dispose()

    problems = asyncio.run(_record(admin))
    shown = _shown(admin.username)
    if admin.totp_enabled:
        print(f"Administrator '{shown}': the password is reset, and two-factor authentication is off.")
        print("Sign in with the new password, then turn two-factor authentication on again and generate "
              "new recovery codes (System > Security).")
    else:
        print(f"Administrator '{shown}': the password is reset (two-factor authentication was off).")
        print("Sign in with the new password.")
    print("Its sessions are ended; its API tokens keep working: docs/RECOVERY.md says what else to do.")
    if problems:
        print(f"reset-admin: The reset is done, but the audit log could not record it ({'; '.join(problems)}).",
              file=sys.stderr)
        return 1
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.emergency",
        description="The emergency command line of UNOS (docs/RECOVERY.md). Run it as root on the host: "
        "sudo /opt/unos/deploy/reset-admin.sh [username]",
    )
    commands = parser.add_subparsers(dest="command", metavar="command", required=True)
    reset = commands.add_parser(
        "reset-admin",
        help="set a new password for an administrator and turn its two-factor authentication off",
        description="Set a new password for an administrator and turn its two-factor authentication off, "
        "recorded in the audit log. On a terminal the new password is asked twice; otherwise it is the "
        "first line of standard input. It is never given on the command line.",
    )
    reset.add_argument("username", nargs="?", help="the administrator; may be left out when UNOS has one")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the command of ``argv`` (sys.argv[1:] when None); its exit status. A wrong command
    line exits with 2 (argparse)."""
    args = _parser().parse_args(argv)
    try:
        return reset_admin(args.username)
    except EmergencyError as exc:
        if isinstance(exc, Interrupted):
            print(file=sys.stderr)  # the prompt's line ends without a line break
        print(f"reset-admin: {exc} {NOTHING_CHANGED}", file=sys.stderr)
        return exc.exit_status


if __name__ == "__main__":
    sys.exit(main())
