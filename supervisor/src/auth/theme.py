"""A user's own theme of the web UI: GET and PUT /auth/me/theme (ADR-0405).

The theme is dark, light or system (the theme of the device the browser runs on). No row is system.
A stored value that is none of the three reads as system: a restored or damaged database is
untrusted (ADR-0144). A write happens only for a real change (ADR-0214), and each real change is one
audit entry (ADR-0320).
"""
from __future__ import annotations

from typing import Literal, get_args

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from ..audit.router import log_action
from ..audit.schemas import AuditAction
from ..database import get_db
from ..openapi import BODY, SIGNED_IN, errors
from .models import User, UserTheme
from .utils import verify_token

router = APIRouter(prefix="/me/theme", tags=["auth"])

Theme = Literal["dark", "light", "system"]
THEMES: tuple[str, ...] = get_args(Theme)
DEFAULT: Theme = "system"


class ThemeBody(BaseModel):
    """The user's theme of the web UI."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"theme": "dark"}]})

    theme: Theme = Field(description="dark, light, or system: the light or dark setting of the user's device.")


async def _stored(db: AsyncSession, user_id: int) -> object:
    return (await db.execute(select(UserTheme.theme).where(UserTheme.user_id == user_id))).scalar_one_or_none()


def _read(stored: object) -> Theme:
    """The theme of a stored value; system for no row and for anything that is not a theme."""
    return stored if isinstance(stored, str) and stored in THEMES else DEFAULT  # type: ignore[return-value]


@router.get("", response_model=ThemeBody, summary="Read your theme", responses=errors(SIGNED_IN))
async def get_theme(user: User = Depends(verify_token), db: AsyncSession = Depends(get_db)):
    """The signed-in user's theme of the web UI; system when they never chose one. Any signed-in user."""
    return ThemeBody(theme=_read(await _stored(db, user.id)))


@router.put("", response_model=ThemeBody, summary="Set your theme", responses=errors(SIGNED_IN, BODY))
async def put_theme(
    request: Request, body: ThemeBody, user: User = Depends(verify_token), db: AsyncSession = Depends(get_db),
):
    """Keep the signed-in user's theme of the web UI; the theme they have already writes nothing, and a
    change is written to the audit log. Any signed-in user."""
    user_id, username = user.id, user.username
    before = _read(await _stored(db, user_id))
    if body.theme != before:
        statement = insert(UserTheme).values(user_id=user_id, theme=body.theme)
        await db.execute(statement.on_conflict_do_update(
            index_elements=[UserTheme.user_id], set_={"theme": body.theme, "updated_at": func.now()}))
        await db.commit()
        try:
            await log_action(
                action=AuditAction.THEME_CHANGED, request=request, username=username,
                resource_type="theme", resource_id=username,
                details=f"Changed the theme from {before} to {body.theme}",
            )
        except OSError:
            pass  # log_action reported the entry; the stored change stands (ADR-0251)
    return ThemeBody(theme=body.theme)
