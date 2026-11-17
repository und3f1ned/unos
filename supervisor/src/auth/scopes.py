"""What an API token may reach: its scope (ADR-0388).

A scope is an access, read only or read and change, and the areas of the API: the first segment of
a path after /api/v1/, one per router prefix of src/main.py. A token with change access to every
area is full and acts as every token did before; any other is limited. ``authenticate_token`` of
src/auth/utils.py checks the scope of every API token, HTTP and WebSocket, with the operation that
the request or the socket asks for (``refusal``); session tokens have no scope.

The scope is kept in api_tokens.permissions, the column that the table already has for it
(ADR-0319): "*" is full; otherwise "read" for read only, then the areas, comma-separated, in
alphabetical order ("read" alone: read only in every area). A value of a restored or damaged
database is read as stored at every use (ADR-0144): one that is not such a list (``parse`` answers
None) refuses its token everywhere.
"""
from __future__ import annotations

import enum
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NamedTuple, Optional

from sqlalchemy import LargeBinary, cast, func

API_PREFIX = "/api/v1/"

# The areas, one per router prefix: tests/test_token_scopes.py fails for a route of another prefix
# and for an area that no route has. A new router's prefix joins them.
AREAS: tuple[str, ...] = (
    "apps", "audit", "auth", "backup", "containers", "files", "groups", "health", "logs", "monitor",
    "network", "notifications", "offsite", "power", "scheduler", "security", "shares", "snapshots",
    "storage", "system", "updates",
)
Area = Literal[AREAS]  # type: ignore[valid-type]  # the request schema's type of an area

# The areas whose change access reaches beyond them (ADR-0388): in apps, containers, scheduler and
# updates it runs programs as root on the host (a privileged app, a container's bind mount, a custom
# task, the source of updates); in backup and offsite it takes the database, with every account's
# password hash and two-factor secret (a backup's download, the off-device copy). The token dialog
# warns of them (WARNED_AREAS of webui/src/lib/api/auth.ts).
WARNED_AREAS: tuple[str, ...] = ("apps", "backup", "containers", "offsite", "scheduler", "updates")

FULL_VALUE = "*"
READ_WORD = "read"

# The 403 details of a limited token, in the order they are checked.
UNREADABLE_SCOPE = "This API token's scope cannot be read: delete it and create a new one"
LIMITED_TOKEN_CANNOT = (
    "A limited API token cannot change accounts, users, API tokens or two-factor authentication, "
    "nor restore a backup: sign in to do it"
)
OUTSIDE_AREAS = "This API token is limited to other areas of the API"
READ_ONLY = "This API token can only read"
SECRET_ANSWER = "A read-only API token cannot read this: the answer holds secrets"


@dataclass(frozen=True)
class Scope:
    read_only: bool
    areas: Optional[frozenset[str]]  # None: every area

    @property
    def full(self) -> bool:
        return not self.read_only and self.areas is None


FULL = Scope(read_only=False, areas=None)


def stored_value(scope: Scope) -> str:
    """The value of api_tokens.permissions for ``scope``; a scope of no area is none (ValueError):
    "read" alone would be read only in every area."""
    if scope.areas is not None and not scope.areas:
        raise ValueError("A scope names at least one area, or every area")
    if scope.full:
        return FULL_VALUE
    words = [READ_WORD] if scope.read_only else []
    return ",".join(words + sorted(scope.areas or ()))


def stored_columns(column) -> tuple:
    """What a query selects to read api_tokens.permissions as stored, without decoding it: its
    SQLite type and its bytes. A value of a restored database that is not UTF-8 would make the
    driver raise when it reads the column as text (the mapped column is deferred for that), and
    the token could then be neither checked nor listed nor deleted."""
    return func.typeof(column), cast(column, LargeBinary)


def stored_text(kind: object, raw: object) -> Optional[str]:
    """The value of ``stored_columns``: the text, or None when it is no UTF-8 text (NULL, a blob,
    bytes that are not UTF-8); ``parse`` reads None as no scope."""
    if kind != "text" or not isinstance(raw, bytes):
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def parse(value: object) -> Optional[Scope]:
    """The scope that a value of api_tokens.permissions holds; None for anything else: not text, an
    empty or repeated item, an unknown word, or "*" beside another item."""
    if not isinstance(value, str):
        return None
    if value == FULL_VALUE:
        return FULL
    items = value.split(",")
    if len(set(items)) != len(items):
        return None
    read_only = READ_WORD in items
    areas = [item for item in items if item != READ_WORD]
    if any(area not in AREAS for area in areas):
        return None
    if not areas:
        return Scope(read_only=True, areas=None) if read_only else None
    return Scope(read_only=read_only, areas=frozenset(areas))


def describe(scope: Scope) -> str:
    """The scope as the audit entry of a token's creation names it."""
    if scope.full:
        return "full access"
    access = "read only" if scope.read_only else "read and change"
    if scope.areas is None:
        return f"{access}, every area"
    return f"{access}: {', '.join(sorted(scope.areas))}"


# ---- Operations ----------------------------------------------------------------------------------


class Kind(str, enum.Enum):
    READ = "read"
    CHANGE = "change"
    SECRET = "secret"  # a read whose answer holds secrets: a read-only token does not get it
    NEVER = "never"    # no limited token, whatever its access and areas


# The operations whose kind is not their method's, and the borderline ones kept as their method says
# (ADR-0388). GET and HEAD read, every other method changes, and every change in the area auth is
# NEVER. tests/test_token_scopes.py compares this table with a copy of its own.
OPERATION_KINDS: dict[tuple[str, str], Kind] = {
    # The database: password hashes, two-factor secrets, the hashes of the API tokens.
    ("GET", "/api/v1/backup/download/{filename}"): Kind.SECRET,
    # SMTP passwords, bot tokens and webhook URLs, as stored.
    ("GET", "/api/v1/notifications/configs"): Kind.SECRET,
    # Reads the UPS with the stored settings.
    ("POST", "/api/v1/power/ups/test"): Kind.READ,
    # Looks for the leftovers of uploads and keeps what it finds in memory.
    ("POST", "/api/v1/files/upload-leftovers/check"): Kind.READ,
    # Send messages with the stored credentials.
    ("POST", "/api/v1/notifications/test/{config_id}"): Kind.CHANGE,
    ("POST", "/api/v1/notifications/send"): Kind.CHANGE,
    # Starts a SMART self-test.
    ("POST", "/api/v1/health/disk/{device:path}/test"): Kind.CHANGE,
    # What the pools hold and the apps print, their passwords included: the area's reads.
    ("GET", "/api/v1/files/download"): Kind.READ,
    ("GET", "/api/v1/containers/{container_id}/logs"): Kind.READ,
    ("GET", "/api/v1/audit/logs/export"): Kind.READ,
    ("GET", "/api/v1/logs/file/{filename}"): Kind.READ,
    # No passphrase, password or private key in these answers.
    ("GET", "/api/v1/scheduler/tasks"): Kind.READ,
    ("GET", "/api/v1/power/ups/config"): Kind.READ,
    ("GET", "/api/v1/offsite/settings"): Kind.READ,
    ("GET", "/api/v1/security/certificates"): Kind.READ,
    # They replace or decide who may do what: the accounts and their tokens, the second factor.
    ("POST", "/api/v1/backup/restore/{filename}"): Kind.NEVER,
    ("POST", "/api/v1/security/two-factor-requirement/enable"): Kind.NEVER,
    ("POST", "/api/v1/security/two-factor-requirement/disable"): Kind.NEVER,
}

# The WebSockets only send: a read-only token opens them. Their areas; None: every area, for the
# events socket, which carries the data of several (stats, problems, scrubs, balances, a share's
# repair, the installs of apps).
SOCKET_AREAS: dict[str, Optional[str]] = {
    "/ws/v1/events": None,
    "/ws/v1/logs/{container_id}": "containers",
    "/ws/v1/journal": "logs",
}
WEBSOCKET = "WEBSOCKET"


class Operation(NamedTuple):
    """What a credential is checked for: an HTTP method, or WEBSOCKET, and the path as routed."""

    method: str
    path: str


def _pattern(template: str) -> re.Pattern[str]:
    """A route's path template as the router matches it: {name} one segment, {name:path} any."""
    parts = re.split(r"(\{[^}]+\})", template)
    regex = "".join(
        (".*" if part.endswith(":path}") else "[^/]+") if part.startswith("{") else re.escape(part)
        for part in parts
    )
    return re.compile(f"^{regex}$")


_OPERATION_PATTERNS = [(method, _pattern(template), kind) for (method, template), kind in OPERATION_KINDS.items()]
_SOCKET_PATTERNS = [(_pattern(template), area) for template, area in SOCKET_AREAS.items()]


def area_of(path: str) -> Optional[str]:
    """The area of an API path; None for a path outside the API's areas."""
    if not path.startswith(API_PREFIX):
        return None
    area = path[len(API_PREFIX):].split("/", 1)[0]
    return area if area in AREAS else None


def kind_of(method: str, path: str) -> Kind:
    method = "GET" if method == "HEAD" else method
    for listed, pattern, kind in _OPERATION_PATTERNS:
        if listed == method and pattern.match(path):
            return kind
    if method == "GET":
        return Kind.READ
    return Kind.NEVER if area_of(path) == "auth" else Kind.CHANGE


_UNKNOWN_SOCKET = object()


def _socket_area(path: str) -> object:
    for pattern, area in _SOCKET_PATTERNS:
        if pattern.match(path):
            return area
    return _UNKNOWN_SOCKET


def refusal(scope: Scope, method: str, path: str) -> Optional[str]:
    """The detail of the 403 that a token of ``scope`` gets for ``method`` (an HTTP method, or
    WEBSOCKET) on ``path`` as routed; None when its scope lets it through."""
    if scope.full:
        return None
    if method == WEBSOCKET:
        area = _socket_area(path)
        if area is _UNKNOWN_SOCKET or (scope.areas is not None and (area is None or area not in scope.areas)):
            return OUTSIDE_AREAS
        return None
    kind = kind_of(method, path)
    if kind is Kind.NEVER:
        return LIMITED_TOKEN_CANNOT
    area = area_of(path)
    if area is None or (scope.areas is not None and area not in scope.areas):
        return OUTSIDE_AREAS
    if scope.read_only and kind is Kind.CHANGE:
        return READ_ONLY
    if scope.read_only and kind is Kind.SECRET:
        return SECRET_ANSWER
    return None


def stored_refusal(stored: object, operation: Operation) -> Optional[str]:
    """``refusal`` for a token whose api_tokens.permissions holds ``stored``."""
    scope = parse(stored)
    if scope is None:
        return UNREADABLE_SCOPE
    return refusal(scope, operation.method, operation.path)


def routed_path(scope: dict) -> str:
    """The path of an ASGI request or socket as the router matched it: without the root path."""
    path, root = scope.get("path", ""), scope.get("root_path", "")
    if root and path.startswith(root):
        path = path[len(root):]
    return path or "/"


# ---- A restore -----------------------------------------------------------------------------------


def unreadable_in(path: Path) -> int:
    """How many API tokens of the database file at ``path`` have a scope that cannot be read; 0
    when the file or its table cannot be read. For a restore's warning: the restore has checked the
    file (no triggers or views) before it reads it."""
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15)) as conn:
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'api_tokens'"
            ).fetchone() is None:
                return 0
            rows = conn.execute("SELECT typeof(permissions), CAST(permissions AS BLOB) FROM api_tokens")
            return sum(parse(stored_text(kind, raw)) is None for kind, raw in rows)
    except sqlite3.Error:
        return 0


def unreadable_warning(count: int) -> str:
    if count == 1:
        return "1 restored API token has a scope that UNOS cannot read: it is refused until it is deleted."
    return f"{count} restored API tokens have a scope that UNOS cannot read: they are refused until they are deleted."
