"""Times with their offset (ADR-0401).

Every point in time that the API answers is ISO 8601 with its offset, in UTC ("2026-11-18T12:00:00Z"
or "+00:00"); a browser reads a time without one as its own local time. The one exception keeps an
offset too: a scheduled task's next run is in the host's time zone (ADR-0266). This module writes the
form of the models, with Z (``utc_text``).

A stored time without an offset is UTC: SQLite's CURRENT_TIMESTAMP is, the supervisor writes UTC into
the DATETIME columns (``db_now``), and the times that it wrote into its files without an offset before
ADR-0401 (the audit log, the update history) are the clock of its container, which runs in UTC.
Nothing stored is rewritten: a reader takes such a time as UTC (``as_utc``, ``parse_time``).
"""
from datetime import datetime, UTC
from typing import Annotated

from pydantic import AfterValidator, WrapSerializer


def utc_now() -> datetime:
    """The current time in UTC, with its zone."""
    return datetime.now(UTC)


def db_now() -> datetime:
    """The current time for a DATETIME column: UTC without a zone, as SQLite's CURRENT_TIMESTAMP.

    SQLAlchemy's SQLite DATETIME keeps a time's clock reading and drops its zone, so a column is
    given UTC only, never a time of another zone.
    """
    return utc_now().replace(tzinfo=None)


def as_utc(value: datetime) -> datetime:
    """``value`` in UTC; a time without a zone is UTC already (a stored one, see above).

    ValueError for a time whose UTC falls outside the calendar (year 1 or 9999 with an offset).
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    try:
        return value.astimezone(UTC)
    except OverflowError:
        raise ValueError(f"{value.isoformat()} is outside the calendar in UTC") from None


def from_epoch(seconds: float) -> datetime:
    """A time in seconds since the epoch (a file's mtime, psutil's boot time) in UTC."""
    return datetime.fromtimestamp(seconds, UTC)


def parse_time(text: str) -> datetime:
    """An ISO 8601 time, as the supervisor stores it, in UTC; one without an offset is UTC.

    ValueError for a text that is not one.
    """
    return as_utc(datetime.fromisoformat(text))


def utc_text(value: datetime) -> str:
    """``value`` as ISO 8601 in UTC with Z, as a model answers it: 2026-11-18T12:00:00.123456Z, without
    a fraction at a whole second (as datetime.isoformat() writes it, Z for +00:00)."""
    text = as_utc(value).isoformat()
    return text.removesuffix("+00:00") + "Z"


def stored_time_text(value: object) -> object:
    """A time of a stored file as the API answers it, in UTC with Z; a value that is not an ISO 8601
    time (None, a damaged one) as stored."""
    if not isinstance(value, str):
        return value
    try:
        return utc_text(parse_time(value))
    except ValueError:
        return value


def with_offset(value: object) -> object:
    """A time that a model keeps as text, with its offset: one without an offset gets UTC's, as
    datetime.isoformat() writes it (+00:00); one with an offset, and a value that is no ISO 8601
    time, stay as they are."""
    if not isinstance(value, str):
        return value
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value
    return value if moment.tzinfo is not None else moment.replace(tzinfo=UTC).isoformat()


# A time that a model keeps as text, as its file holds it: read, and so answered and written back, with
# its offset (with_offset).
UtcText = Annotated[str, AfterValidator(with_offset)]

# A time that a model answers: in UTC, with its offset ("2026-11-07T12:00:00Z"); a time without a
# zone, such as a DATETIME column's, is taken as UTC, also one assigned to the field after validation.
UtcDatetime = Annotated[
    datetime,
    AfterValidator(as_utc),
    WrapSerializer(lambda value, serialize: serialize(as_utc(value) if isinstance(value, datetime) else value)),
]
