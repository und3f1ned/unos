"""Groups of users and their API (ADR-0383). Administrator-only (main.py).

A group is a row of UNOS's database with its members: no host group is made, and its name never
reaches smb.conf or the host. A share's group access (service.py) gives each member whose SMB
account is active the group's level, or the higher level the member has of their own, when
smb.conf is rendered. So a change of the members renders smb.conf again: it is staged and checked
by testparm before the commit and installed after it, and the shares whose access shrank close
their open connections (ADR-0089, ADR-0103). Every change runs under smb_lock() and is audited
(ADR-0030, ADR-0320). A request names a group by its id, which is never handed out twice, with
the name the client saw, and its members by id with their usernames (ADR-0133): an id whose group
or user now has another name is refused (409). Names are unique whatever their letter case.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit.router import log_action
from ..audit.schemas import AuditAction, AuditSeverity
from ..auth.models import User
from ..auth.utils import verify_token
from ..database import get_db
from ..security.validators import sanitize_error_message
from . import account_state, constants, interfaces, models, schemas, service, smb

router = APIRouter(prefix="/groups", tags=["groups"])

GROUP_NAME_RULE = (
    "Group names have 1 to 32 characters: lowercase letters, digits, '_' and '-', starting with a letter"
)
GROUP_NOT_FOUND = "Group not found"
GROUP_VERSION_CONFLICT = "The group was changed meanwhile; reload"

_FRESH = {"populate_existing": True}


def group_name_problem(name: object) -> str | None:
    """Why ``name`` cannot name a group, or None: the pattern of usernames (ADR-0115). The reserved
    names of the host do not apply: no host group is made."""
    if not isinstance(name, str) or not constants.LINUX_NAME_PATTERN.fullmatch(name):
        return GROUP_NAME_RULE
    return None


@dataclass
class GroupChange:
    """What a group mutation did: the answer, and the audit entry (written by the route)."""

    name: str                                   # resource_id of the audit entry
    details: str | None                         # None: nothing changed, no audit entry
    severity: AuditSeverity = AuditSeverity.INFO
    info: schemas.GroupInfo | None = None
    warnings: list[str] = field(default_factory=list)


def _label(value: object) -> str:
    return service._label(value)


async def _group_rows(db: AsyncSession) -> list:
    return (
        await db.execute(
            select(models.UserGroup.id, models.UserGroup.name, models.UserGroup.version)
            .order_by(models.UserGroup.id)
            .execution_options(**_FRESH)
        )
    ).all()


def _find_group(rows: Sequence, name: str, *, ignore: object = None) -> object | None:
    """The id of the group named ``name`` (case-insensitive; the lowest id wins), or None."""
    wanted = name.lower()
    for row in rows:
        if row.id != ignore and isinstance(row.name, str) and row.name.lower() == wanted:
            return row.id
    return None


async def _load_group(db: AsyncSession, group_id: int, name: str) -> models.UserGroup:
    """The group with ``group_id`` as stored now; 404 when there is none, 409 when it is not named
    ``name`` (the request was made for what the group was before a rename)."""
    group = (
        await db.execute(
            select(models.UserGroup).where(models.UserGroup.id == group_id).execution_options(**_FRESH)
        )
    ).scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=404, detail=GROUP_NOT_FOUND)
    if group.name != name:
        raise HTTPException(
            status_code=409, detail=f"Group {group_id} is now '{service._text(group.name)}'; reload the page"
        )
    return group


async def _check_members(db: AsyncSession, entries: Sequence[schemas.GroupMemberEntry]) -> dict[int, str]:
    """The members of a create or PATCH, checked against the users as stored now (ADR-0133): user id
    -> current username. Users whose SMB account is off or needs a password are accepted: the
    group's access counts for them once their account is active."""
    if not entries:
        return {}
    rows = (
        await db.execute(
            select(User.id, User.username, User.is_admin)
            .where(User.id.in_([entry.user_id for entry in entries]))
            .execution_options(**_FRESH)
        )
    ).all()
    users = {row.id: row for row in rows}
    wanted: dict[int, str] = {}
    for entry in entries:
        user = users.get(entry.user_id)
        if user is None:
            raise HTTPException(status_code=400, detail=f"User {entry.user_id} does not exist")
        if user.username != entry.username:
            raise HTTPException(
                status_code=409,
                detail=f"User {entry.user_id} is now '{service._text(user.username)}'; reload the page",
            )
        if user.is_admin:
            raise HTTPException(status_code=400, detail="Administrators cannot use SMB")
        problem = account_state.username_smb_problem(user.username)
        if problem is not None:
            raise HTTPException(status_code=400, detail=problem)
        wanted[entry.user_id] = user.username
    return wanted


async def _replace_members(db: AsyncSession, group_id: object, wanted: Mapping[int, str]) -> list[str]:
    """Make the group's member rows exactly ``wanted`` (user id -> username). A row written for an
    earlier user with the same id is rewritten in place. Returns the diff for the audit entry, such
    as "+carol" and "-bob"."""
    rows = (
        await db.execute(
            select(models.UserGroupMember)
            .where(models.UserGroupMember.group_id == group_id)
            .order_by(models.UserGroupMember.id)
            .execution_options(**_FRESH)
        )
    ).scalars().all()
    added: list[str] = []
    removed: list[str] = []
    kept: set[object] = set()
    for row in rows:
        target = wanted.get(row.user_id) if row.user_id not in kept else None
        if target is None:
            if row.user_id not in kept:
                removed.append(f"-{_label(row.username)}")
            await db.delete(row)
            continue
        kept.add(row.user_id)
        if row.username != target:
            removed.append(f"-{_label(row.username)}")
            added.append(f"+{_label(target)}")
            row.username = target
    for user_id, username in sorted(wanted.items(), key=lambda item: item[1]):
        if user_id not in kept:
            db.add(models.UserGroupMember(group_id=group_id, user_id=user_id, username=username))
            added.append(f"+{_label(username)}")
    return added + removed


async def _infos(db: AsyncSession, only: object = None) -> list[schemas.GroupInfo]:
    """GroupInfo of every group (or of the group with id ``only``), sorted by name."""
    rows = [row for row in await _group_rows(db) if only is None or row.id == only]
    states = await service._account_states(db)
    groups = await service._read_groups(db)
    members = service._listed_members(groups, states)
    share_names = {
        row.id: row.name
        for row in (
            await db.execute(select(models.SmbShare.id, models.SmbShare.name).execution_options(**_FRESH))
        ).all()
    }
    shares: dict[object, list[schemas.GroupShareInfo]] = {}
    for share_id, granted in groups.levels().items():
        if share_id not in share_names:
            continue  # purged at the next start or restore (ADR-0137)
        for group_id, level in granted.items():
            shares.setdefault(group_id, []).append(
                schemas.GroupShareInfo(share=service._text(share_names[share_id]), level=level)
            )
    infos = [
        schemas.GroupInfo(
            id=row.id,
            name=service._text(row.name),
            version=service._stored_version(row.version),
            members=[
                schemas.GroupMemberInfo(
                    user_id=user_id, username=states[user_id].username, smb_state=states[user_id].state
                )
                for user_id in members.get(row.id, ())
            ],
            shares=sorted(shares.get(row.id, []), key=lambda entry: entry.share.lower()),
        )
        for row in rows
    ]
    infos.sort(key=lambda info: info.name.lower())
    return infos


async def _info(db: AsyncSession, group_id: object) -> schemas.GroupInfo:
    infos = await _infos(db, only=group_id)
    if not infos:
        raise HTTPException(status_code=404, detail=GROUP_NOT_FOUND)
    return infos[0]


async def list_groups(db: AsyncSession) -> list[schemas.GroupInfo]:
    """GET /groups."""
    return await _infos(db)


async def get_group(db: AsyncSession, group_id: int) -> schemas.GroupInfo:
    """GET /groups/{group_id}; 404 when there is none."""
    return await _info(db, group_id)


async def create_group(db: AsyncSession, data: schemas.GroupCreate) -> GroupChange:
    """POST /groups. A new group has no share access, so smb.conf stays as it is. Takes smb_lock()."""
    async with service.smb_lock():
        try:
            problem = group_name_problem(data.name)
            if problem is not None:
                raise HTTPException(status_code=400, detail=problem)
            if _find_group(await _group_rows(db), data.name) is not None:
                raise HTTPException(status_code=409, detail=f"Group {data.name} already exists")
            wanted = await _check_members(db, data.members)
            group = models.UserGroup(name=data.name, version=1)
            db.add(group)
            await db.flush()
            group_id = group.id
            # SQLite reuses the highest rowid: rows that a restored database left for this id would
            # give the new group members and access that nobody gave it (as for users, ADR-0132).
            await db.execute(delete(models.UserGroupMember).where(models.UserGroupMember.group_id == group_id))
            await db.execute(
                delete(models.SmbShareGroupAccess).where(models.SmbShareGroupAccess.group_id == group_id)
            )
            for user_id, username in sorted(wanted.items()):
                db.add(models.UserGroupMember(group_id=group_id, user_id=user_id, username=username))
            await _commit(db)
        except Exception:
            await service._rollback(db)
            raise
    details = f"Created group {data.name}"
    if wanted:
        details += f"; members: {', '.join(sorted(_label(name) for name in wanted.values()))}"
    return GroupChange(name=data.name, details=details, info=await _info(db, group_id))


async def update_group(db: AsyncSession, group_id: int, data: schemas.GroupUpdate) -> GroupChange:
    """PATCH /groups/{group_id}: only the fields that are sent change; ``members`` replaces the
    list. A change of the members renders smb.conf again (ADR-0089, ADR-0103); a rename alone does
    not, since smb.conf names no group. Takes smb_lock()."""
    async with service.smb_lock():
        try:
            await service._import_if_needed_locked(db)  # before any change: the import commits on its own
            group = await _load_group(db, group_id, data.name)
            old_name = service._text(group.name)
            sent = data.model_fields_set
            if "expected_version" in sent and data.expected_version != service._stored_version(group.version):
                raise HTTPException(status_code=409, detail=GROUP_VERSION_CONFLICT)
            changes: list[str] = []
            if "new_name" in sent and data.new_name != group.name:
                problem = group_name_problem(data.new_name)
                if problem is not None:
                    raise HTTPException(status_code=400, detail=problem)
                if _find_group(await _group_rows(db), data.new_name, ignore=group_id) is not None:
                    raise HTTPException(status_code=409, detail=f"Group {data.new_name} already exists")
                changes.append(f"name {old_name}→{data.new_name}")
                group.name = data.new_name
            members_changed = False
            if "members" in sent:
                diff = await _replace_members(db, group_id, await _check_members(db, data.members))
                if diff:
                    members_changed = True
                    changes.append(f"members: {', '.join(diff)}")
            if not changes:
                await service._rollback(db)  # nothing was written
                return GroupChange(name=old_name, details=None, info=await _info(db, group_id))
            group.version = service._next_version(group.version)
            await db.flush()
            final_name = service._text(group.name)
            if members_changed:
                result, _ = await service._stage_commit_install(db)
            else:
                await _commit(db)
                result = interfaces.ApplyResult()
        except Exception:
            await service._rollback(db)
            raise
    info = await _info(db, group_id)
    info.warnings = list(result.warnings)
    return GroupChange(
        name=final_name,
        details=f"Changed group {old_name}: {'; '.join(changes)}",
        info=info,
        warnings=info.warnings,
    )


async def delete_group(db: AsyncSession, group_id: int, name: str) -> GroupChange:
    """DELETE /groups/{group_id}?name=: the group, its member rows and its access to shares, whose
    versions grow; smb.conf is rendered again and the shares that lost access close their open
    connections (ADR-0103). The users stay. Takes smb_lock()."""
    async with service.smb_lock():
        try:
            await service._import_if_needed_locked(db)
            group = await _load_group(db, group_id, name)
            group_name = service._text(group.name)
            access = (
                await db.execute(
                    select(
                        models.SmbShareGroupAccess.id,
                        models.SmbShareGroupAccess.share_id,
                        models.SmbShareGroupAccess.level,
                        models.SmbShare.name,
                    )
                    .join(models.SmbShare, models.SmbShare.id == models.SmbShareGroupAccess.share_id, isouter=True)
                    .where(models.SmbShareGroupAccess.group_id == group_id)
                    .execution_options(**_FRESH)
                )
            ).all()
            await db.execute(delete(models.UserGroupMember).where(models.UserGroupMember.group_id == group_id))
            if access:
                await service._delete_group_access_rows(
                    db, [row.id for row in access], {row.share_id for row in access if row.name is not None}
                )
            await db.delete(group)
            await db.flush()
            result, _ = await service._stage_commit_install(db)
        except Exception:
            await service._rollback(db)
            raise
    lost = sorted(
        {f"{service._text(row.name)} ({_label(row.level)})" for row in access if row.name is not None},
        key=str.lower,
    )
    details = f"Deleted group {group_name}"
    if lost:
        details += f"; its access was removed from: {', '.join(lost)}"
    return GroupChange(name=group_name, details=details, severity=AuditSeverity.WARNING,
                       warnings=list(result.warnings))


async def _commit(db: AsyncSession) -> None:
    try:
        await db.commit()
    except Exception as exc:
        await service._rollback(db)
        raise HTTPException(status_code=503, detail=service.BUSY) from exc


# --- the routes ------------------------------------------------------------------------------------


async def _call(coroutine):
    """Await a group operation and map the errors of an apply, as the share routes do."""
    try:
        return await coroutine
    except HTTPException:
        raise
    except service.PoolListUnavailable as exc:
        raise HTTPException(status_code=503, detail=sanitize_error_message(exc)) from exc
    except smb.SambaConfigError as exc:
        raise HTTPException(status_code=500, detail=sanitize_error_message(exc)) from exc


async def _audit(request: Request, actor: str | None, action: AuditAction, change: GroupChange) -> None:
    if change.details is None:
        return
    await log_action(
        action,
        request=request,
        username=actor,
        resource_type="group",
        resource_id=change.name,
        details=change.details,
        severity=change.severity,
    )


@router.get("", response_model=list[schemas.GroupInfo])
async def list_groups_route(_=Depends(verify_token), db: AsyncSession = Depends(get_db)):
    """Every group with its members and the shares it has access to."""
    return await list_groups(db)


@router.post("", response_model=schemas.GroupInfo)
async def create_group_route(
    request: Request, group: schemas.GroupCreate, user=Depends(verify_token), db: AsyncSession = Depends(get_db)
):
    """Make a group of users; give it access on the Shares page."""
    change = await _call(create_group(db, group))
    await _audit(request, user.username, AuditAction.GROUP_CREATED, change)
    return change.info


@router.get("/{group_id}", response_model=schemas.GroupInfo)
async def get_group_route(group_id: int, _=Depends(verify_token), db: AsyncSession = Depends(get_db)):
    """One group."""
    return await get_group(db, group_id)


@router.patch("/{group_id}", response_model=schemas.GroupInfo)
async def update_group_route(
    request: Request,
    group_id: int,
    changes: schemas.GroupUpdate,
    user=Depends(verify_token),
    db: AsyncSession = Depends(get_db),
):
    """Rename a group (new_name) or replace its members; name is its current name (409 otherwise).
    Send expected_version: 409 when the group was changed meanwhile."""
    change = await _call(update_group(db, group_id, changes))
    await _audit(request, user.username, AuditAction.GROUP_UPDATED, change)
    return change.info


@router.delete("/{group_id}", response_model=schemas.GroupDeleted)
async def delete_group_route(
    request: Request,
    group_id: int,
    # The name the request was built for: 409 when the group now has another.
    name: str = Query(max_length=64),
    user=Depends(verify_token),
    db: AsyncSession = Depends(get_db),
):
    """Delete a group: its members lose the access that it gave them; the users stay."""
    change = await _call(delete_group(db, group_id, name))
    await _audit(request, user.username, AuditAction.GROUP_DELETED, change)
    return schemas.GroupDeleted(warnings=change.warnings)
