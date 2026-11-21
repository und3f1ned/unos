"""A user's own dashboard layout: GET, PUT and DELETE /auth/me/dashboard-layout (ADR-0395).

The layout is the list of the dashboard's widgets in the user's order, each with whether it is
hidden. The server knows no catalog of widgets: the web UI ignores an id it has no widget for and
puts a widget that the list lacks at its default place, so a new widget needs no change here. An
empty list is the default layout, which is no row at all. A write happens only for a real change
(ADR-0214), and each real change is one audit entry (ADR-0320).
"""
from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictBool
from pydantic.config import JsonDict
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from ..audit.router import log_action
from ..audit.schemas import AuditAction
from ..database import get_db
from ..openapi import BODY, SIGNED_IN, errors
from .models import DashboardLayout, User
from .utils import verify_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/me/dashboard-layout", tags=["auth"])

MAX_WIDGETS = 64
WIDGET_ID = r"^[a-z0-9][a-z0-9-]{0,31}$"


class Widget(BaseModel):
    """One widget of the dashboard, in the user's order."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        pattern=WIDGET_ID,
        description="The widget's id, such as cpu, memory, network, disk-io, temperature, storage, containers, "
                    "or recent-activity: 1 to 32 lowercase letters, digits and -, not first. The web "
                    "UI ignores an id it has no widget for.",
    )
    hidden: StrictBool = Field(description="Whether the user hid the widget.")


def _each_id_once(widgets: list[Widget]) -> list[Widget]:
    ids = [widget.id for widget in widgets]
    if len(set(ids)) != len(ids):
        raise ValueError("each widget id may appear once")
    return widgets


_LAYOUT_EXAMPLE: JsonDict = {"widgets": [
    {"id": "storage", "hidden": False}, {"id": "cpu", "hidden": False}, {"id": "memory", "hidden": False},
    {"id": "network", "hidden": True}, {"id": "disk-io", "hidden": False}, {"id": "temperature", "hidden": True},
]}


class LayoutBody(BaseModel):
    """The user's new dashboard layout."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [_LAYOUT_EXAMPLE]})

    widgets: Annotated[
        list[Widget],
        Field(
            min_length=1, max_length=MAX_WIDGETS,
            description="The widgets in the user's order, 1 to 64, each id once. A widget that the list lacks "
                        "stands at its default place.",
        ),
        AfterValidator(_each_id_once),
    ]


class LayoutResponse(BaseModel):
    """The user's dashboard layout."""

    model_config = ConfigDict(json_schema_extra={"examples": [_LAYOUT_EXAMPLE]})

    widgets: list[Widget] = Field(
        description="The widgets in the user's order; none: the default layout."
    )


def _dump(widgets: list[Widget]) -> str:
    return json.dumps([{"id": w.id, "hidden": w.hidden} for w in widgets], separators=(",", ":"))


def _read(stored: object) -> list[Widget]:
    """The widgets of a stored value; [] (the default layout) for anything that is not a valid
    layout: a restored or damaged database is untrusted (ADR-0144)."""
    if not isinstance(stored, str):
        return []
    try:
        return LayoutBody.model_validate({"widgets": json.loads(stored)}).widgets
    except (ValueError, RecursionError):
        return []


async def _stored(db: AsyncSession, user_id: int) -> object:
    return (await db.execute(select(DashboardLayout.layout).where(DashboardLayout.user_id == user_id))).scalar_one_or_none()


async def _audit(request: Request, username: str, details: str) -> None:
    try:
        await log_action(
            action=AuditAction.DASHBOARD_LAYOUT_CHANGED, request=request, username=username,
            resource_type="dashboard_layout", resource_id=username, details=details,
        )
    except OSError:
        pass  # log_action reported the entry; the stored change stands (ADR-0251)


@router.get(
    "", response_model=LayoutResponse, summary="Read your dashboard layout", responses=errors(SIGNED_IN),
)
async def get_layout(user: User = Depends(verify_token), db: AsyncSession = Depends(get_db)):
    """The signed-in user's dashboard layout; no widgets means the default, also for a stored layout that
    cannot be read. Any signed-in user."""
    return LayoutResponse(widgets=_read(await _stored(db, user.id)))


@router.put(
    "", response_model=LayoutResponse, summary="Save your dashboard layout", responses=errors(SIGNED_IN, BODY),
)
async def put_layout(
    request: Request, body: LayoutBody, user: User = Depends(verify_token), db: AsyncSession = Depends(get_db),
):
    """Keep the signed-in user's dashboard layout; the same layout again writes nothing, and a change is
    written to the audit log. Any signed-in user."""
    user_id, username = user.id, user.username
    text = _dump(body.widgets)
    if await _stored(db, user_id) != text:
        statement = insert(DashboardLayout).values(user_id=user_id, layout=text)
        await db.execute(statement.on_conflict_do_update(
            index_elements=[DashboardLayout.user_id], set_={"layout": text, "updated_at": func.now()}))
        await db.commit()
        hidden = sum(1 for w in body.widgets if w.hidden)
        count = len(body.widgets)
        await _audit(request, username, f"Arranged the dashboard: {count} widget{'s' if count != 1 else ''}, {hidden} hidden")
    return LayoutResponse(widgets=body.widgets)


@router.delete(
    "", response_model=LayoutResponse, summary="Reset your dashboard layout", responses=errors(SIGNED_IN),
)
async def reset_layout(request: Request, user: User = Depends(verify_token), db: AsyncSession = Depends(get_db)):
    """Back to the default layout: the answer has no widgets. A stored layout that is removed is written to
    the audit log. Any signed-in user."""
    user_id, username = user.id, user.username
    if await _stored(db, user_id) is not None:
        await db.execute(delete(DashboardLayout).where(DashboardLayout.user_id == user_id))
        await db.commit()
        await _audit(request, username, "Reset the dashboard to its default layout")
    return LayoutResponse(widgets=[])
