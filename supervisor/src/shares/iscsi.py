"""iSCSI targets of volumes, served by the kernel's LIO through the host's configfs (ADR-0417).

A target has one LUN: a sparse file ``<volume>/.unos-iscsi/<name>.lun`` in a volume of a pool,
served by LIO's fileio backstore, and CHAP for every initiator that may log in. The targets are rows
of the database (``models.IscsiTarget``, ``models.IscsiInitiator``); their CHAP secrets are in
SECRETS_FILE, mode 0600, outside the database and the configuration backups.

configfs lives in RAM: :func:`apply` makes the kernel's state that of the database, at the start and
after every change, and removes UNOS's targets (IQN_PREFIX) and storage objects (HBA) that the
database no longer has. It reads the state with one host command, prepares the LUN files with one,
and changes configfs with one, whose operations come on its standard input, so that no secret is
on a command line. Everything else in configfs is left as it is.

The pools' module asks :func:`pool_user` and :func:`volume_user` before it unmounts or forgets a
pool or deletes a volume: the index of the targets' volumes is kept in memory, loaded at the start
and changed under the pools' lock with the LUN files.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import secrets as random
import subprocess
import threading
import uuid
import weakref
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import database, host, state_files
from ..network import links
from ..storage import pools
from . import listen
from .models import IscsiInitiator, IscsiTarget

logger = logging.getLogger(__name__)

CONFIGFS = "/sys/kernel/config"
TARGET_ROOT = f"{CONFIGFS}/target"
ISCSI_ROOT = f"{TARGET_ROOT}/iscsi"
# The HBA of UNOS's storage objects: fileio, with the iSCSI port as its index.
HBA = "fileio_3260"
HBA_ROOT = f"{TARGET_ROOT}/core/{HBA}"
IQN_PREFIX = "iqn.2003-01.org.linux-iscsi.unos:"
PORT = 3260
TPG = "tpgt_1"
LUN = "lun_0"
# The name of the links that UNOS makes in configfs (a LUN's storage object, an ACL's mapped LUN).
LINK = "unos"
MODULES = ("target_core_mod", "target_core_file", "iscsi_target_mod")
# The folder of the LUN files in a volume's top folder, root's, mode 0700, kept immutable.
LUN_FOLDER = ".unos-iscsi"
LUN_SUFFIX = ".lun"
SECRETS_FILE = "/data/iscsi_secrets.json"

GIB = 1 << 30
MAX_SIZE_GIB = 65536
MAX_INITIATORS = 16
MAX_TARGETS = 32
HOST_TIMEOUT = 60
# Seconds between two checks of the host's addresses, while a target exists.
FOLLOW_INTERVAL = 300
GENERATED_SECRET_LENGTH = 16
_SECRET_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"

# The target portal group's settings: CHAP for every login, only the listed initiators.
TPG_ATTRIBUTES = (
    ("authentication", "1"),
    ("generate_node_acls", "0"),
    ("cache_dynamic_acls", "0"),
    ("demo_mode_write_protect", "1"),
)

NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
# LIO takes a written value that starts with NULL for "none": no user name or secret starts so.
USER_PATTERN = re.compile(r"(?!NULL)[A-Za-z0-9._:@-]{1,64}")
SECRET_PATTERN = re.compile(r"(?!NULL)[A-Za-z0-9!#%+,\-./:=?@^_~]{12,16}")
_LABEL = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
INITIATOR_PATTERN = re.compile(
    rf"iqn\.[0-9]{{4}}-(?:0[1-9]|1[0-2])\.{_LABEL}(?:\.{_LABEL})*(?::[a-z0-9._:-]+)?"
    r"|eui\.[0-9a-f]{16}|naa\.(?:[0-9a-f]{16}|[0-9a-f]{32})"
)
MAX_INITIATOR_LENGTH = 223
_UID = re.compile(r"[0-9a-f]{32}")
# A LUN file's path as LIO's control parser takes it: no ',', '=' or white space.
_PATH = re.compile(r"/[A-Za-z0-9/._-]+")

# The answers, which webui/tests/iscsi.spec.ts repeats word for word (ADR-0189).
NOT_A_HOST = (
    "iSCSI is off where UNOS runs its host commands locally (UNOS_HOST_EXEC=local): this is not a NAS host"
)
NAME_RULE = "A target name has 1 to 32 lowercase letters, digits and '-', starting with a letter or digit"
USER_RULE = "A CHAP user name has 1 to 64 letters, digits, '.', '-', '_', ':' and '@', and does not start with NULL"
SECRET_RULE = "A CHAP secret has 12 to 16 characters: letters, digits and !#%+,-./:=?@^_~, and does not start with NULL"
SAME_SECRETS = "The mutual CHAP secret must differ from the CHAP secret"
INITIATOR_RULE = (
    "is not an iSCSI name: use iqn.YYYY-MM.<reversed domain>[:<name>], eui.<16 hex digits> or "
    "naa.<16 or 32 hex digits>"
)
NO_INITIATORS = "Allow at least one initiator"
TOO_MANY_INITIATORS = f"A target allows at most {MAX_INITIATORS} initiators"
SIZE_RULE = f"The size of a LUN is a whole number of GiB from 1 to {MAX_SIZE_GIB}"
TOO_MANY_TARGETS = f"UNOS serves at most {MAX_TARGETS} iSCSI targets"
NAME_TAKEN = "An iSCSI target named {name} exists already"
NOT_FOUND = "No iSCSI target is named {name}"
CONFIRM_NAME = "Type the target's name to delete it and the data of its LUN"
NO_SECRET = "No CHAP secret is stored for this target: set a new secret"
POOL_NOT_MOUNTED = "The pool {pool} is not mounted"
VOLUME_MISSING = "The volume {volume} is not in the pool {pool}"
LUN_MISSING = "The LUN file {path} is missing"
LUN_UNSAFE = "The folder {folder} is not root's with mode 700, kept immutable by UNOS"
LUN_WRONG = "The LUN file {path} is not root's with mode 600 and {size} GiB"
NOT_SERVED = "The kernel does not serve this target: change it, or restart UNOS to apply it again"
NO_KERNEL_SUPPORT = (
    "The host's kernel has no iSCSI target (LIO): the modules target_core_mod, target_core_file and "
    "iscsi_target_mod are missing"
)
LUN_EXISTS = "The volume holds a LUN file {path} already: choose another name"
NOT_ENOUGH_SPACE = "The volume has {free} GiB free: a LUN of {size} GiB does not fit"
OVER_QUOTA = "The volume's quota leaves {free} GiB: a LUN of {size} GiB does not fit"
QUOTA_UNREAD = "The volume's quota use cannot be read, so a LUN cannot be checked against it"
DELETE_UNMOUNTED = "The pool {pool} is not mounted: mount it to delete the target and the data of its LUN"
SECRET_NEEDED = "No CHAP secret is stored for this target: set a new secret with this change"


class IscsiError(RuntimeError):
    """A host command of iSCSI failed; the text says which and why, never a secret (HTTP 500)."""


# --- the API's types ---------------------------------------------------------------------------


class IscsiTargetCreate(BaseModel):
    """POST /shares/iscsi/targets: a new target with one LUN in a volume, and CHAP."""

    name: str = Field(description="1 to 32 lowercase letters, digits and '-', starting with a letter or digit.",
                      examples=["backup"])
    pool: str = Field(description="The pool of the volume.", examples=["data"])
    volume: str = Field(description="The volume that holds the LUN's file.", examples=["iscsi"])
    size_gib: int = Field(description=f"The LUN's size in GiB, 1 to {MAX_SIZE_GIB}; its space is not reserved.",
                          examples=[100])
    initiators: list[str] = Field(
        description=f"The iSCSI names of the initiators that may log in, 1 to {MAX_INITIATORS}.",
        examples=[["iqn.1993-08.org.debian:01:5f3c0a1b2c3d"]],
    )
    chap_user: Optional[str] = Field(default=None, description="The CHAP user name; the target's name by default.")
    chap_secret: Optional[str] = Field(
        default=None, repr=False,
        description="The CHAP secret, 12 to 16 characters; UNOS generates one when it is left out.",
    )
    mutual: bool = Field(default=False, description="Mutual CHAP: the target proves itself to the initiator too.")
    mutual_secret: Optional[str] = Field(
        default=None, repr=False,
        description="The secret of mutual CHAP, unlike the CHAP secret; generated when left out.",
    )


class IscsiTargetChange(BaseModel):
    """PATCH /shares/iscsi/targets/{name}: what is left out stays as it is."""

    initiators: Optional[list[str]] = Field(default=None, description="The initiators that may log in, all of them.")
    new_secret: bool = Field(default=False, description="Set a new CHAP secret: chap_secret, or a generated one.")
    chap_secret: Optional[str] = Field(default=None, repr=False, description="The new CHAP secret, with new_secret.")
    mutual: Optional[bool] = Field(default=None, description="Turn mutual CHAP on or off.")
    new_mutual_secret: bool = Field(
        default=False, description="Set a new secret of mutual CHAP: mutual_secret, or a generated one."
    )
    mutual_secret: Optional[str] = Field(
        default=None, repr=False, description="The new secret of mutual CHAP, with new_mutual_secret or mutual."
    )


class IscsiTargetDelete(BaseModel):
    """POST /shares/iscsi/targets/{name}/delete."""

    confirm: str = Field(description="The target's name typed again: its LUN's data is deleted with it.")


class IscsiTargetInfo(BaseModel):
    """A target as GET /shares/iscsi lists it. No secret."""

    name: str = Field(description="The target's name.")
    iqn: str = Field(description="The target's iSCSI name, which initiators connect to.")
    pool: str = Field(description="The pool of the LUN's volume.")
    volume: str = Field(description="The volume that holds the LUN's file.")
    size_gib: int = Field(description="The LUN's size in GiB.")
    path: str = Field(description="The LUN's file on the host.")
    initiators: list[str] = Field(description="The initiators that may log in.")
    chap_user: str = Field(description="The CHAP user name.")
    mutual: bool = Field(description="Whether the target answers with mutual CHAP.")
    served: bool = Field(description="Whether the kernel serves the target now.")
    problem: Optional[str] = Field(default=None, description="Why the target is not served; None while it is.")
    sessions: list[str] = Field(default_factory=list, description="The initiators logged in now.")
    created: Optional[str] = Field(default=None, description="When it was made, ISO 8601 with the UTC offset.")


class IscsiState(BaseModel):
    """GET /shares/iscsi: the targets and where they listen."""

    kernel_support: bool = Field(description="Whether the host's kernel has LIO's iSCSI target.")
    portals: list[str] = Field(description="The addresses and port on which the targets listen: the host's LAN addresses.")
    portals_problem: Optional[str] = Field(default=None, description="Why the host's addresses could not be read.")
    targets: list[IscsiTargetInfo] = Field(description="Every target, by name.")


class IscsiTargetCreated(BaseModel):
    """The answer to a create: the only one that holds the secrets."""

    target: IscsiTargetInfo = Field(description="The new target.")
    chap_secret: str = Field(description="The CHAP secret, shown this once.")
    mutual_secret: Optional[str] = Field(default=None, description="The secret of mutual CHAP, shown this once.")


class IscsiTargetChanged(BaseModel):
    """The answer to a change: a new secret is shown this once."""

    target: IscsiTargetInfo = Field(description="The target as changed.")
    chap_secret: Optional[str] = Field(default=None, description="The new CHAP secret; None when it stayed.")
    mutual_secret: Optional[str] = Field(default=None, description="The new secret of mutual CHAP; None when none is new.")


class IscsiTargetDeleted(BaseModel):
    """The answer to a delete."""

    name: str = Field(description="The deleted target.")
    sessions: list[str] = Field(default_factory=list, description="The initiators whose sessions it ended.")


# --- values --------------------------------------------------------------------------------------


def _bad(detail: str) -> HTTPException:
    return HTTPException(400, detail)


def check_name(name: object) -> str:
    if not isinstance(name, str) or NAME_PATTERN.fullmatch(name) is None:
        raise _bad(NAME_RULE)
    return name


def check_user(user: object) -> str:
    if not isinstance(user, str) or USER_PATTERN.fullmatch(user) is None:
        raise _bad(USER_RULE)
    return user


def check_secret(secret: object) -> str:
    if not isinstance(secret, str) or SECRET_PATTERN.fullmatch(secret) is None:
        raise _bad(SECRET_RULE)
    return secret


def check_initiators(names: object) -> list[str]:
    """The initiators' names in lowercase, each once, in the order given; 400 for a name that is none."""
    if not isinstance(names, list) or not names:
        raise _bad(NO_INITIATORS)
    found: list[str] = []
    for name in names:
        text = name.strip().lower() if isinstance(name, str) else ""
        if len(text) > MAX_INITIATOR_LENGTH or INITIATOR_PATTERN.fullmatch(text) is None:
            shown = name if isinstance(name, str) and len(name) <= MAX_INITIATOR_LENGTH else "This name"
            raise _bad(f"{shown} {INITIATOR_RULE}")
        if text not in found:
            found.append(text)
    if len(found) > MAX_INITIATORS:
        raise _bad(TOO_MANY_INITIATORS)
    return found


def generate_secret() -> str:
    while True:
        secret = "".join(random.choice(_SECRET_ALPHABET) for _ in range(GENERATED_SECRET_LENGTH))
        if SECRET_PATTERN.fullmatch(secret):
            return secret


def lun_path(pool: str, volume: str, name: str) -> str:
    return os.path.join(pools.pool_mountpoint(pool), volume, LUN_FOLDER, f"{name}{LUN_SUFFIX}")


def _iso(when: Optional[datetime]) -> Optional[str]:
    if when is None:
        return None
    return (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).isoformat()


def _refuse_locally() -> None:
    if host.runs_locally():
        raise HTTPException(503, NOT_A_HOST)


# --- the secrets ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Secret:
    chap: str = field(repr=False)
    mutual: Optional[str] = field(default=None, repr=False)


def _read_secrets(data: Any) -> dict[str, Secret]:
    """SECRETS_FILE's JSON: {uid: {"chap": ..., "mutual": ... or null}}. An entry with a value that is
    no secret is left out (its target is then not served); a file of another shape is kept as .bad."""
    if not isinstance(data, dict):
        raise ValueError("not an object")
    found = {}
    for uid, entry in data.items():
        if not isinstance(entry, dict):
            raise ValueError("an entry is not an object")
        chap, mutual = entry.get("chap"), entry.get("mutual")
        if (_UID.fullmatch(str(uid)) is None or not isinstance(chap, str) or SECRET_PATTERN.fullmatch(chap) is None
                or (mutual is not None and (not isinstance(mutual, str) or SECRET_PATTERN.fullmatch(mutual) is None))):
            continue
        found[uid] = Secret(chap, mutual)
    return found


def load_secrets() -> dict[str, Secret]:
    """The stored secrets by target id; none without the file. OSError when it cannot be opened."""
    return state_files.load_json(Path(SECRETS_FILE), _read_secrets, dict, what="the CHAP secrets of the iSCSI targets")


def write_secrets(stored: dict[str, Secret]) -> None:
    """Replace SECRETS_FILE, mode 0600, synced (blocking: a worker thread)."""
    data = {uid: {"chap": s.chap, "mutual": s.mutual} for uid, s in sorted(stored.items())}
    state_files.write_json(Path(SECRETS_FILE), data, sync=True, mode=0o600)


# --- the index of the targets' volumes, for the pools' module ------------------------------------


_index_lock = threading.Lock()
_index: dict[str, tuple[str, str]] = {}  # target name -> (pool, volume)


def _set_index(entries: dict[str, tuple[str, str]]) -> None:
    with _index_lock:
        _index.clear()
        _index.update(entries)


def _index_put(name: str, pool: str, volume: str) -> None:
    with _index_lock:
        _index[name] = (pool, volume)


def _index_drop(name: str) -> None:
    with _index_lock:
        _index.pop(name, None)


def pool_user(pool: str) -> Optional[str]:
    """The targets whose LUN is in a volume of POOL, as "a, b", or None (storage/pools.py refuses to
    unmount or forget the pool meanwhile)."""
    with _index_lock:
        names = sorted(name for name, (p, _) in _index.items() if p == pool)
    return ", ".join(names) or None


def volume_user(path: str) -> Optional[str]:
    """The targets whose LUN's volume is the folder PATH or lies in it, as "a, b", or None
    (storage/pools.py refuses to delete it meanwhile)."""
    path = os.path.normpath(path)
    with _index_lock:
        entries = list(_index.items())
    names = sorted(
        name for name, (pool, volume) in entries
        if (where := os.path.join(pools.pool_mountpoint(pool), volume)) == path or where.startswith(path + "/")
    )
    return ", ".join(names) or None


async def load_index() -> None:
    """Read the targets' volumes from the database (the lifespan's start, before the first request)."""
    async with database.async_session() as db:
        rows = (await db.execute(select(IscsiTarget))).scalars().all()
    _set_index({row.name: (row.pool, row.volume) for row in rows})


# --- the host's scripts --------------------------------------------------------------------------

# Prints the state of UNOS's part of configfs, one fact per line; $1 is configfs, $2 IQN_PREFIX,
# $3 HBA. It only reads.
READ_SCRIPT = r"""t=$1/target
[ -d "$t/iscsi" ] && echo loaded
if [ -d "$t/core/$3" ]; then
  echo hba
  for o in "$t/core/$3"/*; do
    [ -d "$o" ] || continue
    printf 'object %s %s %s\n' "${o##*/}" "$(cat "$o/enable" 2>/dev/null)" "$(cat "$o/udev_path" 2>/dev/null)"
  done
fi
for d in "$t/iscsi/$2"*; do
  [ -d "$d" ] || continue
  n=${d##*/}
  printf 'target %s\n' "$n"
  for g in "$d"/tpgt_*; do
    [ -d "$g" ] || continue
    p=${g##*/}
    printf 'tpg %s %s %s %s %s\n' "$n" "$p" "$(cat "$g/enable" 2>/dev/null)" \
      "$(cat "$g/attrib/authentication" 2>/dev/null)" "$(cat "$g/attrib/generate_node_acls" 2>/dev/null)"
    for l in "$g"/lun/lun_*; do
      [ -d "$l" ] || continue
      printf 'lun %s %s %s\n' "$n" "$p" "${l##*/}"
      for x in "$l"/*; do
        [ -L "$x" ] && printf 'lunlink %s %s %s %s\n' "$n" "$p" "${l##*/}" "${x##*/}"
      done
    done
    for x in "$g"/np/*; do
      [ -d "$x" ] && printf 'portal %s %s %s\n' "$n" "$p" "${x##*/}"
    done
    for a in "$g"/acls/*; do
      [ -d "$a" ] || continue
      printf 'acl %s %s %s\n' "$n" "$p" "${a##*/}"
      for m in "$a"/lun_*; do
        [ -d "$m" ] || continue
        printf 'mapped %s %s %s %s\n' "$n" "$p" "${a##*/}" "${m##*/}"
        for x in "$m"/*; do
          [ -L "$x" ] && printf 'mappedlink %s %s %s %s %s\n' "$n" "$p" "${a##*/}" "${m##*/}" "${x##*/}"
        done
      done
      if [ -f "$a/info" ] && ! grep -q 'No active iSCSI Session' "$a/info"; then
        printf 'session %s %s %s\n' "$n" "$p" "${a##*/}"
      fi
    done
  done
done
exit 0
"""

# The LUN files' scripts work from inside the folder LUN_FOLDER, entered with cd -P and checked with
# pwd -P to be the folder of that name in the volume, not a link: an SMB user who can write in the
# volume's top folder can rename entries there, but not the shell's current folder, so what follows
# acts on the folder that was checked. Once the folder is immutable, it cannot be renamed and no
# entry in it can change, so the path that LIO opens stays the checked one.

# Checks the LUN files of the targets to serve and keeps their folders immutable: $1 is LUN_FOLDER,
# then a volume's path, a file name and its size in bytes for each target. For each it prints ok,
# missing (no folder or no file), unsafe (a folder that is a link, moved, or not root's with mode
# 700) or wrong (a file that is a link, not a regular file, not root's with mode 600, or of another
# size), with the file name.
PREPARE_SCRIPT = r"""folder=$1
shift
while [ "$#" -ge 3 ]; do
  volume=$1 name=$2 size=$3
  shift 3
  verdict=$(
    cd -P -- "$volume" 2>/dev/null || { echo missing; exit; }
    here=$(pwd -P)
    if [ -L "$folder" ]; then echo unsafe; exit; fi
    if [ ! -d "$folder" ]; then echo missing; exit; fi
    cd -P -- "$folder" 2>/dev/null || { echo unsafe; exit; }
    if [ "$(pwd -P)" != "$here/$folder" ]; then echo unsafe; exit; fi
    if [ "$(stat -c '%u %a' -- .)" != "0 700" ]; then echo unsafe; exit; fi
    flags=$(lsattr -d -- .) || { echo unsafe; exit; }
    # The patterns' opening parentheses: a case inside $(...) needs them in bash's sh.
    case ${flags%% *} in
      (*i*) ;;
      (*) chattr +i -- . || { echo unsafe; exit; } ;;
    esac
    if [ "$(pwd -P)" != "$here/$folder" ]; then echo unsafe; exit; fi
    if [ -L "$name" ] || [ ! -e "$name" ]; then echo missing; exit; fi
    if [ ! -f "$name" ] || [ "$(stat -c '%u %a %s' -- "$name")" != "0 600 $size" ]; then echo wrong; exit; fi
    echo ok
  )
  echo "$verdict $name"
done
exit 0
"""

# Makes a LUN file: $1 the volume's path, $2 LUN_FOLDER, $3 the file name, $4 its size in bytes. The
# folder is made root's with mode 0700 when missing (00700: a folder made in a setgid folder, such
# as an SMB share's, inherits the setgid bit, which a numeric mode clears only with its leading 0),
# the file a new one (noclobber), mode 0600, sparse, and the folder is immutable again at the end.
# Exit 3: the folder is not safe; 4: the file exists; 1: a command failed.
CREATE_SCRIPT = r"""folder=$2 name=$3 size=$4
cd -P -- "$1" || exit 1
here=$(pwd -P)
if [ -L "$folder" ]; then echo "$here/$folder is a symbolic link" >&2; exit 3; fi
made=
if [ ! -e "$folder" ]; then mkdir -m 700 -- "$folder" || exit 1; made=1; fi
cd -P -- "$folder" || exit 1
if [ "$(pwd -P)" != "$here/$folder" ]; then echo "$here/$folder moved" >&2; exit 3; fi
if [ -n "$made" ]; then chmod 00700 . || exit 1; fi
if [ "$(stat -c '%u %a' -- .)" != "0 700" ]; then
  echo "$here/$folder is not a folder of root with mode 700" >&2; exit 3
fi
flags=$(lsattr -d -- .) || exit 1
case ${flags%% *} in *i*) chattr -i -- . || exit 1 ;; esac
if [ -e "$name" ] || [ -L "$name" ]; then rc=4
elif (set -C; : > "$name"); then
  chmod 600 "$name" && truncate -s "$size" -- "$name"
  rc=$?
  [ "$rc" -eq 0 ] || rm -f -- "$name"
else rc=1
fi
chattr +i -- . || exit 1
exit "$rc"
"""

# Deletes a LUN file: $1 the volume's path, $2 LUN_FOLDER, $3 the file name. The folder goes too
# when it is empty then, and stays immutable otherwise. Exit 3: the folder is a link or moved.
DELETE_SCRIPT = r"""folder=$2 name=$3
cd -P -- "$1" || exit 1
here=$(pwd -P)
if [ -L "$folder" ]; then echo "$here/$folder is a symbolic link" >&2; exit 3; fi
[ -d "$folder" ] || exit 0
cd -P -- "$folder" || exit 1
if [ "$(pwd -P)" != "$here/$folder" ]; then echo "$here/$folder moved" >&2; exit 3; fi
flags=$(lsattr -d -- .) || exit 1
case ${flags%% *} in *i*) chattr -i -- . || exit 1 ;; esac
rm -f -- "$name"
rc=$?
if [ -n "$(ls -A .)" ]; then chattr +i -- . || exit 1; exit "$rc"; fi
cd .. && rmdir -- "$folder" 2>/dev/null
exit "$rc"
"""

# Changes configfs: $1 is TARGET_ROOT. Each line of standard input is an operation, its path and
# its value, separated by tabs: mkdir, rmdir, write (the value into an attribute), link (a link at
# the path to the value) and unlink. Every path lies below $1, without "..". The value is written
# with the shell's builtin printf, through a pipe, so that it is on no command line. The first
# operation that fails ends the script; its error names the operation and the path, not the value.
APPLY_SCRIPT = r"""root=$1
tab=$(printf '\t')
while IFS=$tab read -r op path value; do
  case $path in
    "$root"/*) ;;
    *) printf 'refused: %s %s\n' "$op" "$path" >&2; exit 2 ;;
  esac
  case $path in
    */..|*/../*) printf 'refused: %s %s\n' "$op" "$path" >&2; exit 2 ;;
  esac
  case $op in
    mkdir) mkdir -- "$path" ;;
    rmdir) rmdir -- "$path" ;;
    write) printf '%s' "$value" | cat > "$path" ;;
    link)
      case $value in
        "$root"/*) ;;
        *) printf 'refused: %s %s\n' "$op" "$path" >&2; exit 2 ;;
      esac
      ln -s -- "$value" "$path" ;;
    unlink) rm -- "$path" ;;
    *) printf 'refused: %s %s\n' "$op" "$path" >&2; exit 2 ;;
  esac || { printf 'failed: %s %s\n' "$op" "$path" >&2; exit 1; }
done
exit 0
"""

# Loads LIO's modules and makes sure configfs is mounted and the iSCSI fabric registered: $1 is
# configfs, then the modules.
MODULES_SCRIPT = r"""c=$1
shift
modprobe -a "$@" || exit 1
if [ ! -d "$c/target" ]; then
  mountpoint -q "$c" || mount -t configfs configfs "$c" || exit 1
fi
[ -d "$c/target/iscsi" ] || mkdir "$c/target/iscsi"
"""


def _run(cmd: list[str], *, input: Optional[str] = None) -> host.HostResult:
    """A host command (sync); IscsiError when the host cannot be asked. Never with a secret in argv."""
    try:
        return host.run_on_host(cmd, timeout=HOST_TIMEOUT, input=input, errors="replace")
    except subprocess.TimeoutExpired as exc:
        raise IscsiError(f"The host did not answer within {HOST_TIMEOUT} seconds: {cmd[3] if len(cmd) > 3 else cmd[0]}") from exc
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise IscsiError(f"The host could not be asked: {exc}") from exc


def _why(result: host.HostResult) -> str:
    return (result.stderr or result.stdout).strip() or f"exit status {result.returncode}"


# --- the state of configfs -----------------------------------------------------------------------


@dataclass
class Acl:
    mapped: dict[str, list[str]] = field(default_factory=dict)  # mapped LUN -> its links
    session: bool = False


@dataclass
class Tpg:
    enabled: bool = False
    chap: bool = False  # authentication 1 and generate_node_acls 0
    luns: dict[str, list[str]] = field(default_factory=dict)  # LUN -> its links
    portals: set[str] = field(default_factory=set)
    acls: dict[str, Acl] = field(default_factory=dict)


@dataclass
class TargetState:
    tpgs: dict[str, Tpg] = field(default_factory=dict)


@dataclass
class ObjectState:
    enabled: bool
    udev_path: str


@dataclass
class State:
    loaded: bool = False
    hba: bool = False
    objects: dict[str, ObjectState] = field(default_factory=dict)
    targets: dict[str, TargetState] = field(default_factory=dict)


def parse_state(text: str) -> State:
    """The state that READ_SCRIPT printed; a line it cannot read is left out."""
    state = State()
    for line in text.splitlines():
        kind, _, rest = line.partition(" ")
        parts = rest.split(" ")
        if kind == "loaded":
            state.loaded = True
        elif kind == "hba":
            state.hba = True
        elif kind == "object" and len(parts) >= 2:
            state.objects[parts[0]] = ObjectState(parts[1] == "1", " ".join(parts[2:]))
        elif kind == "target" and len(parts) == 1:
            state.targets.setdefault(parts[0], TargetState())
        elif kind == "tpg" and len(parts) >= 3:
            tpg = state.targets.setdefault(parts[0], TargetState()).tpgs.setdefault(parts[1], Tpg())
            tpg.enabled = parts[2] == "1"
            tpg.chap = parts[3:5] == ["1", "0"]
        elif len(parts) >= 3 and kind in ("lun", "lunlink", "portal", "acl", "mapped", "mappedlink", "session"):
            tpg = state.targets.setdefault(parts[0], TargetState()).tpgs.setdefault(parts[1], Tpg())
            if kind == "lun":
                tpg.luns.setdefault(parts[2], [])
            elif kind == "lunlink" and len(parts) == 4:
                tpg.luns.setdefault(parts[2], []).append(parts[3])
            elif kind == "portal":
                tpg.portals.add(parts[2])
            elif kind == "acl":
                tpg.acls.setdefault(parts[2], Acl())
            elif kind == "mapped" and len(parts) == 4:
                tpg.acls.setdefault(parts[2], Acl()).mapped.setdefault(parts[3], [])
            elif kind == "mappedlink" and len(parts) == 5:
                tpg.acls.setdefault(parts[2], Acl()).mapped.setdefault(parts[3], []).append(parts[4])
            elif kind == "session":
                tpg.acls.setdefault(parts[2], Acl()).session = True
    return state


def read_state() -> State:
    """UNOS's part of configfs on the host (sync)."""
    result = _run(["/bin/sh", "-c", READ_SCRIPT, "unos-iscsi-read", CONFIGFS, IQN_PREFIX, HBA])
    if not result.ok:
        raise IscsiError(f"The kernel's iSCSI configuration could not be read: {_why(result)}")
    return parse_state(result.stdout)


def kernel_support(state: State) -> bool:
    """Whether the host's kernel has LIO's modules (loaded, or there to load)."""
    if state.loaded:
        return True
    result = _run(["modprobe", "-n", "-q", "-a", *MODULES])
    return result.ok


def lan_portals() -> list[str]:
    """TCP 3260 on each LAN address of the host's enabled, broadcast-capable interfaces other than
    Docker's (sync), from ``ip -j addr show``: not an address that is still tentative or failed its
    duplicate address detection, which the kernel does not let a socket bind. listen.Unreadable
    when the interfaces cannot be read."""
    try:
        result = host.run_on_host(listen.IP_COMMAND, timeout=listen.HOST_TIMEOUT)
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError) as exc:
        raise listen.Unreadable(str(exc) or type(exc).__name__) from exc
    if not result.ok:
        raise listen.Unreadable(_why(result))
    try:
        data = json.loads(result.stdout or "[]")
    except ValueError as exc:
        raise listen.Unreadable("ip printed no JSON") from exc
    found: dict[str, tuple[int, int]] = {}
    for entry in data if isinstance(data, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("ifname"), str):
            continue
        flags = {flag for flag in entry.get("flags") or () if isinstance(flag, str)}
        if "UP" not in flags or "BROADCAST" not in flags or "POINTOPOINT" in flags or links.is_hidden(entry["ifname"]):
            continue
        for info in entry.get("addr_info") or ():
            if (not isinstance(info, dict) or info.get("family") not in ("inet", "inet6")
                    or not isinstance(info.get("local"), str) or info.get("tentative") or info.get("dadfailed")
                    or not listen.is_lan_address(info["local"])):
                continue
            address = ipaddress.ip_address(info["local"].split("%", 1)[0])
            portal = f"[{address}]:{PORT}" if address.version == 6 else f"{address}:{PORT}"
            found[portal] = (address.version, int(address))
    return sorted(found, key=found.__getitem__)


# --- what to serve, and the operations that make configfs so -------------------------------------


@dataclass(frozen=True)
class Serve:
    """A target to serve, with its secrets."""

    name: str
    uid: str
    iqn: str
    path: str
    size: int  # bytes
    chap_user: str
    initiators: tuple[str, ...]
    secret: Secret = field(repr=False)


Op = tuple[str, str, str]


def _teardown_acl(g: str, name: str, acl: Acl) -> list[Op]:
    ops: list[Op] = []
    for mapped, names in sorted(acl.mapped.items()):
        ops += [("unlink", f"{g}/acls/{name}/{mapped}/{link}", "") for link in names]
        ops.append(("rmdir", f"{g}/acls/{name}/{mapped}", ""))
    ops.append(("rmdir", f"{g}/acls/{name}", ""))
    return ops


def _teardown_tpg(base: str, name: str, tpg: Tpg) -> list[Op]:
    g = f"{base}/{name}"
    ops: list[Op] = [("write", f"{g}/enable", "0")] if tpg.enabled else []
    for acl_name, acl in sorted(tpg.acls.items()):
        ops += _teardown_acl(g, acl_name, acl)
    for lun, names in sorted(tpg.luns.items()):
        ops += [("unlink", f"{g}/lun/{lun}/{link}", "") for link in names]
        ops.append(("rmdir", f"{g}/lun/{lun}", ""))
    ops += [("rmdir", f"{g}/np/{portal}", "") for portal in sorted(tpg.portals)]
    ops.append(("rmdir", g, ""))
    return ops


def _teardown_target(iqn: str, target: TargetState) -> list[Op]:
    base = f"{ISCSI_ROOT}/{iqn}"
    ops: list[Op] = []
    for name, tpg in sorted(target.tpgs.items()):
        ops += _teardown_tpg(base, name, tpg)
    ops.append(("rmdir", base, ""))
    return ops


def _make_object(serve: Serve) -> list[Op]:
    o = f"{HBA_ROOT}/{serve.uid}"
    return [
        ("mkdir", o, ""),
        ("write", f"{o}/control", f"fd_dev_name={serve.path},fd_dev_size={serve.size}"),
        ("write", f"{o}/udev_path", serve.path),
        ("write", f"{o}/wwn/vpd_unit_serial", str(uuid.UUID(serve.uid))),
        ("write", f"{o}/enable", "1"),
        # After enable, when the file is open: UNMAP punches holes in it.
        ("write", f"{o}/attrib/emulate_tpu", "1"),
        ("write", f"{o}/attrib/emulate_tpws", "1"),
    ]


def _auth(g: str, initiator: str, serve: Serve) -> list[Op]:
    """The CHAP values of a new ACL; mutual CHAP only with its secret."""
    auth = f"{g}/acls/{initiator}/auth"
    ops: list[Op] = [("write", f"{auth}/userid", serve.chap_user), ("write", f"{auth}/password", serve.secret.chap)]
    if serve.secret.mutual is not None:
        ops += [("write", f"{auth}/userid_mutual", serve.chap_user),
                ("write", f"{auth}/password_mutual", serve.secret.mutual)]
    return ops


def _target_ops(serve: Serve, target: Optional[TargetState], portals: Optional[list[str]], rewrite: bool) -> list[Op]:
    base = f"{ISCSI_ROOT}/{serve.iqn}"
    g = f"{base}/{TPG}"
    ops: list[Op] = []
    if target is None:
        ops.append(("mkdir", base, ""))
        target = TargetState()
    for name, other in sorted(target.tpgs.items()):
        if name != TPG:
            ops += _teardown_tpg(base, name, other)
    tpg = target.tpgs.get(TPG)
    fresh = tpg is None
    if tpg is None:
        ops.append(("mkdir", g, ""))
        tpg = Tpg()
    if fresh or not tpg.chap:
        ops += [("write", f"{g}/attrib/{name}", value) for name, value in TPG_ATTRIBUTES]
        ops.append(("write", f"{g}/param/AuthMethod", "CHAP"))
    lun = f"{g}/lun/{LUN}"
    for other, names in sorted(tpg.luns.items()):
        if other != LUN:
            ops += [("unlink", f"{g}/lun/{other}/{link}", "") for link in names]
            ops.append(("rmdir", f"{g}/lun/{other}", ""))
    if LUN not in tpg.luns:
        ops.append(("mkdir", lun, ""))
    if not tpg.luns.get(LUN):
        ops.append(("link", f"{lun}/{LINK}", f"{HBA_ROOT}/{serve.uid}"))
    if portals is not None:
        wanted = set(portals)
        ops += [("mkdir", f"{g}/np/{portal}", "") for portal in portals if portal not in tpg.portals]
        ops += [("rmdir", f"{g}/np/{portal}", "") for portal in sorted(tpg.portals - wanted)]
    for name, acl in sorted(tpg.acls.items()):
        if name not in serve.initiators:
            ops += _teardown_acl(g, name, acl)
    for name in serve.initiators:
        acl = tpg.acls.get(name)
        mapped = f"{g}/acls/{name}/{LUN}"
        if acl is not None and rewrite:
            # New CHAP values: the ACL is made anew, which ends its session, so that an initiator
            # logged in with the old secret does not stay in.
            ops += _teardown_acl(g, name, acl)
            acl = None
        if acl is None:
            ops.append(("mkdir", f"{g}/acls/{name}", ""))
            ops += _auth(g, name, serve)
            acl = Acl()
        if LUN not in acl.mapped:
            ops.append(("mkdir", mapped, ""))
        if not acl.mapped.get(LUN):
            ops.append(("link", f"{mapped}/{LINK}", lun))
    if fresh or not tpg.enabled:
        ops.append(("write", f"{g}/enable", "1"))
    return ops


def plan_parts(state: State, serve: list[Serve], portals: Optional[list[str]],
               rewrite: frozenset[str] = frozenset(), keep: frozenset[str] = frozenset()
               ) -> tuple[list[Op], dict[str, list[Op]]]:
    """The operations that make UNOS's part of configfs serve SERVE and nothing else: those that
    remove what is not served (UNOS's other targets and storage objects), and those of each target
    to serve, by its name, which run one target at a time.

    ``portals`` None: the host's addresses could not be read, so the targets keep theirs and a new
    one gets none. ``rewrite``: the names of the targets whose ACLs are made anew with their CHAP
    values (a new secret). ``keep``: the IQNs and storage object ids that are left as they are,
    neither served nor removed (a target whose volume could not be checked now). A storage object
    that is not enabled or names another file is made anew, with its target.
    """
    wanted = {s.iqn: s for s in serve}
    by_uid = {s.uid: s for s in serve}
    remade = {
        uid for uid, obj in state.objects.items()
        if uid in by_uid and (not obj.enabled or obj.udev_path != by_uid[uid].path)
    }
    common: list[Op] = []
    for iqn, target in sorted(state.targets.items()):
        s = wanted.get(iqn)
        if iqn not in keep and (s is None or s.uid in remade):
            common += _teardown_target(iqn, target)
    for uid in sorted(state.objects):
        if uid not in keep and (uid not in by_uid or uid in remade):
            common.append(("rmdir", f"{HBA_ROOT}/{uid}", ""))
    if serve and not state.hba:
        common.append(("mkdir", HBA_ROOT, ""))
    each: dict[str, list[Op]] = {}
    for s in serve:
        ops = _make_object(s) if s.uid not in state.objects or s.uid in remade else []
        existing = None if s.uid in remade else state.targets.get(s.iqn)
        ops += _target_ops(s, existing, portals, s.name in rewrite)
        if ops:
            each[s.name] = ops
    return common, each


def plan(state: State, serve: list[Serve], portals: Optional[list[str]], rewrite: frozenset[str] = frozenset(),
         keep: frozenset[str] = frozenset()) -> list[Op]:
    """plan_parts' operations in the order they run."""
    common, each = plan_parts(state, serve, portals, rewrite, keep)
    return common + [op for s in serve for op in each.get(s.name, [])]


def run_ops(ops: list[Op]) -> None:
    """Run the operations on the host (sync); IscsiError naming the one that failed, never a value."""
    for op, path, value in ops:
        if "\t" in path or "\n" in path or "\t" in value or "\n" in value:
            raise IscsiError(f"Refused a value with a tab or a line break for {path}")
    text = "".join(f"{op}\t{path}\t{value}\n" for op, path, value in ops)
    result = _run(["/bin/sh", "-c", APPLY_SCRIPT, "unos-iscsi-apply", TARGET_ROOT], input=text)
    if not result.ok:
        raise IscsiError(f"The kernel's iSCSI configuration could not be changed: {_why(result)}")


def _load_modules() -> None:
    result = _run(["/bin/sh", "-c", MODULES_SCRIPT, "unos-iscsi-modules", CONFIGFS, *MODULES])
    if not result.ok:
        raise IscsiError(f"LIO's modules could not be loaded: {_why(result)}")


def _prepare(candidates: list[tuple[str, str, int]]) -> dict[str, str]:
    """PREPARE_SCRIPT for (volume path, target name, size in bytes): file name -> its verdict."""
    if not candidates:
        return {}
    args: list[str] = []
    for volume_path, name, size in candidates:
        args += [volume_path, f"{name}{LUN_SUFFIX}", str(size)]
    result = _run(["/bin/sh", "-c", PREPARE_SCRIPT, "unos-iscsi-prepare", LUN_FOLDER, *args])
    if not result.ok:
        raise IscsiError(f"The LUN files could not be checked: {_why(result)}")
    verdicts = {}
    for line in result.stdout.splitlines():
        verdict, _, name = line.partition(" ")
        verdicts[name] = verdict
    return verdicts


# --- apply ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    """A target's row with its initiators, read once."""

    name: str
    uid: str
    iqn: str
    pool: str
    volume: str
    size_gib: int
    chap_user: str
    mutual: bool
    initiators: tuple[str, ...]
    created: Optional[str]


# Why each target was not served at the last apply, and the portals it bound.
_problems: dict[str, str] = {}
_last_portals: Optional[list[str]] = None
# One lock per event loop (an asyncio.Lock binds to the loop it is first used in; tests run several
# loops, cf. security.firewall).
_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()


def _lock() -> asyncio.Lock:
    """One apply or change at a time."""
    loop = asyncio.get_running_loop()
    lock = _locks.get(loop)
    if lock is None:
        lock = _locks[loop] = asyncio.Lock()
    return lock


def _row_problem(row: Row) -> Optional[str]:
    """Why a row cannot be served whatever the host has: a value a restored database may hold."""
    try:
        check_name(row.name)
        check_user(row.chap_user)
        pools.validate_pool_name(row.pool)
        pools.validate_volume_name(row.volume)
        check_initiators(list(row.initiators))
    except HTTPException as exc:
        return f"The stored target is not valid: {exc.detail}"
    if (_UID.fullmatch(row.uid) is None or row.iqn != f"{IQN_PREFIX}{row.name}.{row.uid[:8]}"
            or not isinstance(row.size_gib, int) or not 1 <= row.size_gib <= MAX_SIZE_GIB
            or _PATH.fullmatch(lun_path(row.pool, row.volume, row.name)) is None):
        return "The stored target is not valid"
    return None


def _volumes(pool: str) -> dict[str, Any]:
    return {volume.name: volume for volume in pools.list_volumes(pool)}


def _apply_sync(rows: list[Row], stored: dict[str, Secret], rewrite: frozenset[str]) -> dict[str, str]:
    """Make configfs serve ROWS that can be served, and nothing else of UNOS's; why each other is not.

    A target that cannot be served (its pool not mounted, its LUN file or secret missing or wrong)
    is removed from configfs; one whose volume could not be checked now (btrfs failed or did not
    answer) is left as it is. Each target's operations run apart: one that the kernel refuses is
    removed again and named, and the others are served. IscsiError when configfs cannot be read or
    UNOS's other targets cannot be removed.
    """
    global _last_portals
    state = read_state()
    problems: dict[str, str] = {}
    keep: set[str] = set()
    pool_volumes: dict[str, dict[str, Any] | tuple[str, bool]] = {}
    candidates: list[tuple[Row, Secret]] = []
    for row in rows:
        problem = _row_problem(row)
        secret = stored.get(row.uid)
        if problem is None and (secret is None or (row.mutual and secret.mutual is None)):
            problem = NO_SECRET
        if problem is None:
            if row.pool not in pool_volumes:
                try:
                    pool_volumes[row.pool] = _volumes(row.pool)
                except (pools.PoolNotFoundError, pools.PoolConflictError):
                    pool_volumes[row.pool] = (POOL_NOT_MOUNTED.format(pool=row.pool), False)
                except Exception as exc:
                    pool_volumes[row.pool] = (f"The volumes of the pool {row.pool} could not be read: {exc}", True)
            volumes = pool_volumes[row.pool]
            if isinstance(volumes, tuple):
                problem, transient = volumes
                if transient:
                    keep.update((row.iqn, row.uid))
            elif row.volume not in volumes:
                problem = VOLUME_MISSING.format(volume=row.volume, pool=row.pool)
        if problem is not None:
            problems[row.name] = problem
            continue
        assert secret is not None
        candidates.append((row, secret if row.mutual else Secret(secret.chap)))
    verdicts = _prepare([
        (os.path.join(pools.pool_mountpoint(row.pool), row.volume), row.name, row.size_gib * GIB)
        for row, _ in candidates
    ])
    serve: list[Serve] = []
    for row, secret in candidates:
        path = lun_path(row.pool, row.volume, row.name)
        verdict = verdicts.get(f"{row.name}{LUN_SUFFIX}")
        if verdict == "ok":
            serve.append(Serve(row.name, row.uid, row.iqn, path, row.size_gib * GIB, row.chap_user,
                               row.initiators, secret))
        elif verdict == "unsafe":
            problems[row.name] = LUN_UNSAFE.format(folder=os.path.dirname(path))
        elif verdict == "wrong":
            problems[row.name] = LUN_WRONG.format(path=path, size=row.size_gib)
        else:
            problems[row.name] = LUN_MISSING.format(path=path)
    if not serve and not (set(state.targets) - keep) and not (set(state.objects) - keep):
        _last_portals = None
        return problems
    if serve and not state.loaded:
        _load_modules()
        state = read_state()
        if not state.loaded:
            raise IscsiError(NO_KERNEL_SUPPORT)
    portals: Optional[list[str]] = None
    if serve:
        try:
            portals = lan_portals()
        except listen.Unreadable as exc:
            logger.warning("The host's addresses could not be read; the iSCSI targets keep their portals: %s", exc)
    common, each = plan_parts(state, serve, portals, rewrite, frozenset(keep))
    if common:
        run_ops(common)
    refused: list[Serve] = []
    for s in serve:
        if s.name not in each:
            continue
        try:
            run_ops(each[s.name])
        except IscsiError as exc:
            problems[s.name] = str(exc)
            refused.append(s)
    if refused:
        _take_back(refused)
    served = [s for s in serve if s not in refused]
    if common or each:
        logger.info(
            "Applied the iSCSI targets: %d served (%s), %d not served, on %s",
            len(served), ", ".join(s.name for s in served) or "none", len(problems),
            ", ".join(portals) if portals is not None else "the portals they had",
        )
    if portals is not None or not served:
        _last_portals = portals if served else None
    return problems


def _take_back(refused: list[Serve]) -> None:
    """Remove from configfs what was made of targets whose operations the kernel refused, so that
    none is left half made, without its CHAP values; logs, never raises."""
    try:
        state = read_state()
        ops: list[Op] = []
        for s in refused:
            if s.iqn in state.targets:
                ops += _teardown_target(s.iqn, state.targets[s.iqn])
            if s.uid in state.objects:
                ops.append(("rmdir", f"{HBA_ROOT}/{s.uid}", ""))
        if ops:
            run_ops(ops)
    except IscsiError as exc:
        logger.warning("What was made of iSCSI targets that the kernel refused was not removed: %s", exc)


async def _rows(db: AsyncSession) -> list[Row]:
    targets = (await db.execute(select(IscsiTarget).order_by(IscsiTarget.name))).scalars().all()
    initiators = (await db.execute(select(IscsiInitiator).order_by(IscsiInitiator.id))).scalars().all()
    by_target: dict[int, list[str]] = {}
    for initiator in initiators:
        by_target.setdefault(initiator.target_id, []).append(initiator.name)
    return [
        Row(t.name, t.uid, t.iqn, t.pool, t.volume, t.size_gib, t.chap_user, bool(t.mutual),
            tuple(by_target.get(t.id, [])), _iso(t.created_at))
        for t in targets
    ]


async def apply(*, rewrite: frozenset[str] = frozenset(), exclude: frozenset[str] = frozenset()) -> dict[str, str]:
    """Apply the database's targets (the caller holds _lock()): IscsiError when the host fails.
    ``exclude``: targets left out of configfs, as a delete does before it removes the row."""
    async with database.async_session() as db:
        rows = await _rows(db)
    _set_index({row.name: (row.pool, row.volume) for row in rows})
    stored = await asyncio.to_thread(load_secrets)
    problems = await asyncio.to_thread(
        _apply_sync, [row for row in rows if row.name not in exclude], stored, rewrite
    )
    _problems.clear()
    _problems.update(problems)
    return problems


async def restore_targets(*, after_restore: bool = False) -> None:
    """Apply the database's targets at the start (the lifespan, in the background) and, with
    ``after_restore``, after a restore of the database: then nothing runs when neither the database
    before it (the index) nor the restored one has a target. Logs, never raises."""
    if host.runs_locally():
        return
    try:
        async with _lock():
            if after_restore:
                with _index_lock:
                    had = bool(_index)
                async with database.async_session() as db:
                    if not had and not await _rows(db):
                        return
            problems = await apply()
    except Exception as exc:
        logger.warning("The iSCSI targets could not be restored: %s", exc)
        return
    for name, problem in sorted(problems.items()):
        logger.warning("iSCSI target %s is not served: %s", name, problem)


# --- the check of the host's addresses -----------------------------------------------------------

_follow_task: Optional[asyncio.Task] = None


async def _follow() -> None:
    failed: Optional[str] = None
    while True:
        await asyncio.sleep(FOLLOW_INTERVAL)
        # Nothing served, nothing to follow: an unserved target is applied again at the start, at a
        # change and when its pool is mounted, never from here, whose apply would ask btrfs about
        # the pools and wake their disks (ADR-0378).
        if _last_portals is None:
            continue
        try:
            portals = await asyncio.to_thread(lan_portals)
            if portals == _last_portals:
                failed = None
                continue
            async with _lock():
                await apply()
            failed = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if str(exc) != failed:
                logger.warning("The iSCSI targets could not follow the host's addresses: %s", exc)
            failed = str(exc)


async def start_follow() -> None:
    """Apply again when the host's LAN addresses changed, every FOLLOW_INTERVAL seconds while a target
    is served: one host command that reads, and no log line when nothing changed (ADR-0214)."""
    global _follow_task
    if host.runs_locally() or _follow_task is not None:
        return
    _follow_task = asyncio.create_task(_follow(), name="unos-iscsi-follow")


async def stop_follow() -> None:
    global _follow_task
    task, _follow_task = _follow_task, None
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# --- the API's logic -----------------------------------------------------------------------------


def _info(row: Row, state: Optional[State]) -> IscsiTargetInfo:
    target = state.targets.get(row.iqn) if state is not None else None
    tpg = target.tpgs.get(TPG) if target is not None else None
    served = bool(tpg and tpg.enabled and tpg.luns.get(LUN))
    sessions = sorted(name for name, acl in tpg.acls.items() if acl.session) if tpg else []
    return IscsiTargetInfo(
        name=row.name, iqn=row.iqn, pool=row.pool, volume=row.volume, size_gib=row.size_gib,
        path=lun_path(row.pool, row.volume, row.name), initiators=list(row.initiators),
        chap_user=row.chap_user, mutual=row.mutual, served=served,
        problem=None if served else _problems.get(row.name, NOT_SERVED), sessions=sessions, created=row.created,
    )


def _observe() -> tuple[State, bool, list[str], Optional[str]]:
    state = read_state()
    support = kernel_support(state)
    try:
        return state, support, lan_portals(), None
    except listen.Unreadable as exc:
        return state, support, [], f"The host's network interfaces could not be read: {exc}"


def _host_failed(exc: Exception) -> HTTPException:
    logger.warning("An iSCSI request failed on the host: %s", exc)
    return HTTPException(500, str(exc))


async def list_state(db: AsyncSession) -> IscsiState:
    """GET /shares/iscsi."""
    _refuse_locally()
    rows = await _rows(db)
    try:
        state, support, portals, problem = await asyncio.to_thread(_observe)
    except IscsiError as exc:
        raise _host_failed(exc) from exc
    return IscsiState(kernel_support=support, portals=portals, portals_problem=problem,
                      targets=[_info(row, state) for row in rows])


async def _row(db: AsyncSession, name: str) -> Row:
    for row in await _rows(db):
        if row.name == name:
            return row
    raise HTTPException(404, NOT_FOUND.format(name=name))


def _make_lun(name: str, pool: str, volume: str, size_gib: int) -> str:
    """Check the volume and its space and make the LUN file, under the pools' lock, and put the
    target into the index (sync). The LUN file's path."""
    with pools._lock:
        try:
            info = _volumes(pool).get(volume)
        except pools.PoolNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except pools.PoolConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        if info is None:
            raise HTTPException(404, VOLUME_MISSING.format(volume=volume, pool=pool))
        path = lun_path(pool, volume, name)
        if _PATH.fullmatch(path) is None:
            raise HTTPException(400, f"The LUN's path {path} has characters that LIO cannot take")
        size = size_gib * GIB
        result = _run(["df", "-B1", "--output=avail", "--", info.path])
        free = result.stdout.split()[-1] if result.ok and result.stdout.split() else ""
        if not free.isdigit():
            raise IscsiError(f"The free space of {info.path} could not be read: {_why(result)}")
        if size > int(free):
            raise HTTPException(409, NOT_ENOUGH_SPACE.format(free=int(free) // GIB, size=size_gib))
        if info.quota is not None:
            if info.used is None:
                raise HTTPException(409, QUOTA_UNREAD)
            if size > info.quota - info.used:
                raise HTTPException(409, OVER_QUOTA.format(free=max(info.quota - info.used, 0) // GIB, size=size_gib))
        made = _run(["/bin/sh", "-c", CREATE_SCRIPT, "unos-iscsi-create", info.path, LUN_FOLDER,
                     f"{name}{LUN_SUFFIX}", str(size)])
        if made.returncode == 4:
            raise HTTPException(409, LUN_EXISTS.format(path=path))
        if made.returncode == 3:
            raise HTTPException(409, LUN_UNSAFE.format(folder=os.path.dirname(path)))
        if not made.ok:
            raise IscsiError(f"The LUN file {path} could not be made: {_why(made)}")
        _index_put(name, pool, volume)
        return path


def _delete_lun(name: str, pool: str, volume: str) -> None:
    """Delete the LUN file (sync); IscsiError when it could not be."""
    volume_path = os.path.join(pools.pool_mountpoint(pool), volume)
    result = _run(["/bin/sh", "-c", DELETE_SCRIPT, "unos-iscsi-delete", volume_path, LUN_FOLDER, f"{name}{LUN_SUFFIX}"])
    if not result.ok:
        raise IscsiError(f"The LUN file {lun_path(pool, volume, name)} could not be deleted: {_why(result)}")


def _store_secret(uid: str, secret: Optional[Secret]) -> None:
    stored = load_secrets()
    if secret is None:
        stored.pop(uid, None)
    else:
        stored[uid] = secret
    write_secrets(stored)


async def _current_info(db: AsyncSession, name: str) -> IscsiTargetInfo:
    row = await _row(db, name)
    try:
        state = await asyncio.to_thread(read_state)
    except IscsiError:
        state = None
    return _info(row, state)


async def create_target(db: AsyncSession, spec: IscsiTargetCreate) -> IscsiTargetCreated:
    """POST /shares/iscsi/targets: the LUN file, the secret, the rows, then the apply; all of it is
    undone when the apply fails or does not serve the target."""
    _refuse_locally()
    name = check_name(spec.name)
    pool = pools.validate_pool_name(spec.pool)
    volume = pools.validate_volume_name(spec.volume)
    if not isinstance(spec.size_gib, int) or not 1 <= spec.size_gib <= MAX_SIZE_GIB:
        raise _bad(SIZE_RULE)
    initiators = check_initiators(spec.initiators)
    user = check_user(spec.chap_user if spec.chap_user is not None else name)
    chap = check_secret(spec.chap_secret) if spec.chap_secret else generate_secret()
    mutual = None
    if spec.mutual:
        mutual = check_secret(spec.mutual_secret) if spec.mutual_secret else generate_secret()
        while mutual == chap and not spec.mutual_secret:
            mutual = generate_secret()
        if mutual == chap:
            raise _bad(SAME_SECRETS)
    async with _lock():
        rows = await _rows(db)
        if any(row.name == name for row in rows):
            raise HTTPException(409, NAME_TAKEN.format(name=name))
        if len(rows) >= MAX_TARGETS:
            raise HTTPException(409, TOO_MANY_TARGETS)
        uid = uuid.uuid4().hex
        iqn = f"{IQN_PREFIX}{name}.{uid[:8]}"
        try:
            await asyncio.to_thread(_make_lun, name, pool, volume, spec.size_gib)
        except IscsiError as exc:
            raise _host_failed(exc) from exc
        stored = False
        try:
            await asyncio.to_thread(_store_secret, uid, Secret(chap, mutual))
            stored = True
            target = IscsiTarget(name=name, uid=uid, iqn=iqn, pool=pool, volume=volume, size_gib=spec.size_gib,
                                 chap_user=user, mutual=mutual is not None)
            db.add(target)
            await db.flush()
            db.add_all(IscsiInitiator(target_id=target.id, name=initiator) for initiator in initiators)
            await db.commit()
            problems = await apply()
            if name in problems:
                raise IscsiError(f"The new target is not served: {problems[name]}")
        except BaseException as exc:
            await _undo_create(db, name, uid, pool, volume, stored)
            if isinstance(exc, IscsiError):
                raise _host_failed(exc) from exc
            raise
        return IscsiTargetCreated(target=await _current_info(db, name), chap_secret=chap, mutual_secret=mutual)


async def _undo_create(db: AsyncSession, name: str, uid: str, pool: str, volume: str, stored: bool) -> None:
    """Take a create back: the rows, configfs, the secret, the LUN file and the index entry, each step
    whether the one before it worked or not (the new LUN file holds no data yet)."""

    async def rows() -> None:
        await db.rollback()
        target = (await db.execute(select(IscsiTarget).where(IscsiTarget.uid == uid))).scalar_one_or_none()
        if target is not None:
            await db.execute(delete(IscsiInitiator).where(IscsiInitiator.target_id == target.id))
            await db.execute(delete(IscsiTarget).where(IscsiTarget.id == target.id))
            await db.commit()

    steps = [rows(), apply()]
    if stored:
        steps.append(asyncio.to_thread(_store_secret, uid, None))
    steps.append(asyncio.to_thread(_delete_lun, name, pool, volume))
    for step in steps:
        try:
            await step
        except Exception as exc:
            logger.warning("A step of taking back the iSCSI target %s that could not be made failed: %s", name, exc)
    _index_drop(name)


@dataclass
class Change:
    target: IscsiTargetInfo
    chap_secret: Optional[str]
    mutual_secret: Optional[str]
    details: list[str]


async def change_target(db: AsyncSession, name: str, spec: IscsiTargetChange) -> Change:
    """PATCH /shares/iscsi/targets/{name}: the initiators, a new secret, mutual CHAP. ``details`` is
    empty when nothing changed."""
    _refuse_locally()
    initiators = check_initiators(spec.initiators) if spec.initiators is not None else None
    async with _lock():
        row = await _row(db, name)
        stored = await asyncio.to_thread(load_secrets)
        current = stored.get(row.uid)
        details: list[str] = []
        chap = current.chap if current is not None else None
        new_chap = None
        if spec.new_secret:
            new_chap = check_secret(spec.chap_secret) if spec.chap_secret else generate_secret()
            chap = new_chap
        mutual_on = row.mutual if spec.mutual is None else spec.mutual
        mutual = current.mutual if current is not None and mutual_on else None
        new_mutual = None
        if mutual_on and (spec.new_mutual_secret or mutual is None or spec.mutual_secret):
            new_mutual = check_secret(spec.mutual_secret) if spec.mutual_secret else generate_secret()
            while new_mutual == chap and not spec.mutual_secret:
                new_mutual = generate_secret()
            mutual = new_mutual
        if chap is None:
            raise HTTPException(409, SECRET_NEEDED)
        if mutual is not None and mutual == chap:
            raise _bad(SAME_SECRETS)
        if initiators is not None and list(initiators) != list(row.initiators):
            details.append(f"initiators {', '.join(initiators)}")
        if new_chap is not None:
            details.append("a new CHAP secret")
        if mutual_on != row.mutual:
            details.append("mutual CHAP on" if mutual_on else "mutual CHAP off")
        elif new_mutual is not None:
            details.append("a new secret of mutual CHAP")
        if not details:
            return Change(await _current_info(db, name), None, None, [])
        new_values = new_chap is not None or new_mutual is not None or mutual_on != row.mutual
        if new_values:
            await asyncio.to_thread(_store_secret, row.uid, Secret(chap, mutual))
        target = (await db.execute(select(IscsiTarget).where(IscsiTarget.name == name))).scalar_one()
        target.mutual = mutual_on
        if initiators is not None:
            await db.execute(delete(IscsiInitiator).where(IscsiInitiator.target_id == target.id))
            db.add_all(IscsiInitiator(target_id=target.id, name=initiator) for initiator in initiators)
        await db.commit()
        try:
            # New CHAP values make the target's ACLs anew, which ends the sessions of the old ones.
            await apply(rewrite=frozenset({name}) if new_values else frozenset())
        except IscsiError as exc:
            # The change is stored and applies at the next start or change: its new secrets are
            # answered all the same, never to be shown again, and the target says why it is not served.
            logger.warning("The change of the iSCSI target %s is stored but not applied: %s", name, exc)
            _problems[name] = str(exc)
        return Change(await _current_info(db, name), new_chap, new_mutual, details)


async def delete_target(db: AsyncSession, name: str, confirm: str) -> tuple[Row, list[str]]:
    """POST /shares/iscsi/targets/{name}/delete: configfs, the LUN file, the rows and the secret.
    The row and the initiators that had a session."""
    _refuse_locally()
    async with _lock():
        row = await _row(db, name)
        if confirm != name:
            raise _bad(CONFIRM_NAME)
        try:
            volumes = await asyncio.to_thread(_volumes, row.pool)
        except (pools.PoolNotFoundError, pools.PoolConflictError) as exc:
            raise HTTPException(409, DELETE_UNMOUNTED.format(pool=row.pool)) from exc
        if row.volume not in volumes:
            raise HTTPException(409, VOLUME_MISSING.format(volume=row.volume, pool=row.pool))
        try:
            state = await asyncio.to_thread(read_state)
            target = state.targets.get(row.iqn)
            tpg = target.tpgs.get(TPG) if target is not None else None
            sessions = sorted(n for n, acl in tpg.acls.items() if acl.session) if tpg else []
            await apply(exclude=frozenset({name}))
            await asyncio.to_thread(_delete_lun, name, row.pool, row.volume)
        except IscsiError as exc:
            raise _host_failed(exc) from exc
        target_row = (await db.execute(select(IscsiTarget).where(IscsiTarget.name == name))).scalar_one()
        await db.execute(delete(IscsiInitiator).where(IscsiInitiator.target_id == target_row.id))
        await db.execute(delete(IscsiTarget).where(IscsiTarget.id == target_row.id))
        await db.commit()
        _index_drop(name)
        _problems.pop(name, None)
        try:
            await asyncio.to_thread(_store_secret, row.uid, None)
        except OSError as exc:
            logger.warning("The CHAP secret of the deleted iSCSI target %s stays in %s: %s", name, SECRETS_FILE, exc)
        return row, sessions


def describe(row: Row | IscsiTargetInfo) -> str:
    """A target in an audit entry: no secret."""
    return (f"{row.name} ({row.iqn}), a LUN of {row.size_gib} GiB in the volume {row.volume} of the pool "
            f"{row.pool}, CHAP user {row.chap_user}{', mutual CHAP' if row.mutual else ''}, "
            f"initiators {', '.join(row.initiators)}")
