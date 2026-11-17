"""Database tables of the UNOS SMB model (Phase 1 slice 2).

New tables only: init_db() runs create_all, which creates missing tables in
existing databases (there is no migration framework, and columns are never
added to existing tables). A restore checks the first four in the extracted
backup database and refuses one that lacks them (ADR-0319); smb_uids it
makes anew from the live database's, adding the backup's rows that it can
read only for uids still missing (ADR-0353); the options and the group tables
it checks when the backup has them. SQLite does not enforce
foreign keys here (no PRAGMA foreign_keys): code that deletes a user or a
share deletes the dependent rows itself, in the same transaction
(SQLite reuses the highest rowid, so an orphan row would attach to the next
user; access rows also keep the username they were written for). Restored
databases come from uploaded archives and may lack every constraint: values
read from these tables are validated again before they reach smb.conf or a
host command.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..database import Base
from .constants import SMB_UID_MAX, SMB_UID_MIN


class SmbShare(Base):
    """An SMB share; smb.conf is rendered from these rows."""

    __tablename__ = "smb_shares"

    id = Column(Integer, primary_key=True)
    # Samba share names are case-insensitive: NOCASE makes UNIQUE and lookups so (names are ASCII).
    name = Column(String(32, collation="NOCASE"), nullable=False, unique=True)
    path = Column(Text, nullable=False)
    comment = Column(Text, nullable=False, default="", server_default="")
    browseable = Column(Boolean, nullable=False, default=True, server_default="1")
    # Guests (no password) may read the share; they can never write.
    guest_ok = Column(Boolean, nullable=False, default=False, server_default="0")
    # JSON list of messages from the one-time smb.conf import; NULL when none or dismissed.
    import_notes = Column(Text, nullable=True)
    # Incremented on every change of the share or of its access rows (PATCH expected_version).
    version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=True, onupdate=func.now())

    access = relationship(
        "SmbShareAccess", back_populates="share", cascade="all, delete-orphan", lazy="selectin"
    )
    # The share's protocol options (ADR-0380); no row: every option is off.
    options = relationship(
        "SmbShareOptions", back_populates="share", cascade="all, delete-orphan", lazy="selectin", uselist=False
    )
    # The groups that have access.
    group_access = relationship(
        "SmbShareGroupAccess", back_populates="share", cascade="all, delete-orphan", lazy="selectin"
    )


class SmbShareAccess(Base):
    """Access of one UNOS user to one share. No row: no access."""

    __tablename__ = "smb_share_access"
    __table_args__ = (
        UniqueConstraint("share_id", "user_id", name="uq_smb_share_access_share_user"),
        CheckConstraint("level IN ('read', 'write')", name="ck_smb_share_access_level"),
        Index("ix_smb_share_access_user_id", "user_id"),
    )

    id = Column(Integer, primary_key=True)
    share_id = Column(Integer, ForeignKey("smb_shares.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # users.username when the row was written; a row whose user now has another name is purged.
    username = Column(String(64), nullable=False)
    level = Column(String(8), nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    share = relationship("SmbShare", back_populates="access")


class SmbShareOptions(Base):
    """The protocol options of one share (ADR-0380), each off by default. No row: every option is
    off; a row exists only while one is on. Deleted with its share, in the same transaction."""

    __tablename__ = "smb_share_options"

    share_id = Column(Integer, ForeignKey("smb_shares.id", ondelete="CASCADE"), primary_key=True)
    # The share's section requires SMB3 encryption: a client that cannot encrypt is refused there.
    encryption_required = Column(Boolean, nullable=False, default=False, server_default="0")
    # A Mac can use the share as a Time Machine backup disk (vfs_fruit).
    time_machine = Column(Boolean, nullable=False, default=False, server_default="0")
    # Windows' Previous Versions list the snapshots of the share's pool or volume (vfs_shadow_copy2).
    previous_versions = Column(Boolean, nullable=False, default=False, server_default="0")

    share = relationship("SmbShare", back_populates="options")


class UserGroup(Base):
    """A group of UNOS users (ADR-0385). It lives in this database only: no host group is made, and
    smb.conf names its members, not the group. Its rows of user_group_members and of
    smb_share_group_access are deleted with it, in the same transaction. Its id is never handed out
    again (AUTOINCREMENT), so that a request for a deleted group never reaches a new one; a new
    group still deletes the rows that a restored database left for its id."""

    __tablename__ = "user_groups"
    __table_args__ = {"sqlite_autoincrement": True}

    id = Column(Integer, primary_key=True)
    # The rule of usernames (shares.groups.GROUP_NAME_RULE), unique whatever the letter case.
    name = Column(String(32, collation="NOCASE"), nullable=False, unique=True)
    # Incremented on every change of the group (PATCH expected_version).
    version = Column(Integer, nullable=False, default=1, server_default="1")


class UserGroupMember(Base):
    """A user's membership of a group. Like an access row, it keeps the username it was written for:
    a row whose user now has another name, or is an administrator, counts for nothing and is purged
    (ADR-0137)."""

    __tablename__ = "user_group_members"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_user_group_members_group_user"),
        Index("ix_user_group_members_user_id", "user_id"),
    )

    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("user_groups.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    username = Column(String(64), nullable=False)


class SmbShareGroupAccess(Base):
    """Access of one group to one share: each member with an active SMB account gets the
    level, or the higher level that the member has of their own. No row: the group has no access."""

    __tablename__ = "smb_share_group_access"
    __table_args__ = (
        UniqueConstraint("share_id", "group_id", name="uq_smb_share_group_access_share_group"),
        CheckConstraint("level IN ('read', 'write')", name="ck_smb_share_group_access_level"),
        Index("ix_smb_share_group_access_group_id", "group_id"),
    )

    id = Column(Integer, primary_key=True)
    share_id = Column(Integer, ForeignKey("smb_shares.id", ondelete="CASCADE"), nullable=False)
    group_id = Column(Integer, ForeignKey("user_groups.id", ondelete="CASCADE"), nullable=False)
    level = Column(String(8), nullable=False)

    share = relationship("SmbShare", back_populates="group_access")


class SmbAccount(Base):
    """SMB state of one UNOS user. No row: SMB was never allowed for the user."""

    __tablename__ = "smb_accounts"
    __table_args__ = (
        CheckConstraint("password_state IN ('set', 'needed')", name="ck_smb_accounts_password_state"),
        CheckConstraint(
            f"uid IS NULL OR uid BETWEEN {SMB_UID_MIN} AND {SMB_UID_MAX}",
            name="ck_smb_accounts_uid_range",
        ),
    )

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    # Host (Linux and Samba) account name; users.username when the row was made.
    linux_name = Column(String(32), nullable=False, unique=True)
    # Allocated at the first activation, then kept for this user; never reused.
    uid = Column(Integer, nullable=True, unique=True)
    # An administrator allowed SMB access for this user.
    enabled = Column(Boolean, nullable=False, default=False, server_default="0")
    # "set": Samba has the user's current UNOS password; "needed": Samba has no entry.
    password_state = Column(String(8), nullable=False, default="needed", server_default="needed")
    password_set_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=True, onupdate=func.now())


class SmbState(Base):
    """The single row (id 1) of installation-wide SMB state."""

    __tablename__ = "smb_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_smb_state_singleton"),
        CheckConstraint(
            "pending_import_source IS NULL OR pending_import_source IN ('live')",
            name="ck_smb_state_pending_import_source",
        ),
    )

    id = Column(Integer, primary_key=True)
    # Next uid to try. Only ever increases, so uids are never reused.
    next_uid = Column(Integer, nullable=False, default=SMB_UID_MIN, server_default=str(SMB_UID_MIN))
    # When the shares of smb.conf were imported (once). NULL: not yet.
    shares_imported_at = Column(DateTime, nullable=True)
    # JSON list of messages from that import for the administrator; NULL when none or dismissed.
    import_report = Column(Text, nullable=True)
    # Set by a restore whose database is not authoritative for SMB (ADR-0319): the next
    # import reads the live file ('live'), never turns guest access on, and clears it.
    pending_import_source = Column(String(8), nullable=True)


class SmbUid(Base):
    """A uid that UNOS handed out on this NAS, and the host account name it went to (ADR-0353).

    Files on the pools keep their owner's uid after the account is gone, so a row outlives its
    account and is never deleted: an activation writes it with the row's uid, the reconcile for a
    stale marked host account before removing it and for a row it lacks, a user's deletion for the
    row it deletes, and a restore makes the table anew: the live rows, the uids it restores, then
    the backup's rows for uids still missing. A restored account keeps a uid recorded here only
    under the name recorded with it, and no activation hands one out again.
    """

    __tablename__ = "smb_uids"
    __table_args__ = (
        CheckConstraint(f"uid BETWEEN {SMB_UID_MIN} AND {SMB_UID_MAX}", name="ck_smb_uids_uid_range"),
    )

    uid = Column(Integer, primary_key=True, autoincrement=False)
    linux_name = Column(String(32), nullable=False)


class SmbUidName(Base):
    """Each name a uid went by through renames (ADR-0368): a rename of an account with a uid writes
    the old and the new name, in its commit. smb_uids keeps the first name; a restored account
    keeps a recorded uid under any name of the two tables. Like smb_uids, no row is ever deleted,
    and a restore makes the table anew: the live rows, then the backup's rows for uids that this
    NAS did not know.
    """

    __tablename__ = "smb_uid_names"
    __table_args__ = (
        CheckConstraint(f"uid BETWEEN {SMB_UID_MIN} AND {SMB_UID_MAX}", name="ck_smb_uid_names_uid_range"),
    )

    uid = Column(Integer, primary_key=True, autoincrement=False)
    linux_name = Column(String(32), primary_key=True)


async def get_smb_state(db: AsyncSession) -> SmbState:
    """The single SmbState row; added to the session (flushed, not committed) when missing."""
    state = await db.get(SmbState, 1)
    if state is None:
        state = SmbState(id=1, next_uid=SMB_UID_MIN)
        db.add(state)
        await db.flush()
    return state
