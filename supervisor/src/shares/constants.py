"""Fixed values of the UNOS SMB model (Phase 1 slice 2).

The group id and the user ids end up in the ownership of files on the pools
and in the host's account files: they must never change once in use. They lie
above UID_MAX and GID_MAX of login.defs (60000), so that a hand-made useradd
or groupadd never picks one, below SUB_UID_MIN (100000), and in a block that
systemd never allocates (65536-524287).
"""

from __future__ import annotations

import functools
import re
import stat
import unicodedata
from pathlib import Path
from typing import TypeGuard

# Host group of every SMB account UNOS creates; share folders belong to it.
SMB_GROUP_NAME = "unos-smb"
SMB_GROUP_GID = 70000

# User ids of SMB accounts, allocated by UNOS from this range and never reused.
SMB_UID_MIN = 70000
SMB_UID_MAX = 79999

# User id of the apps that run in the group unos-smb (ADR-0274): what they
# write in share folders belongs to it, and so may a share's folder, which
# their images give to their user when they start. No SMB account and no app
# outside the group has it.
SMB_APP_UID = 80000

# A host account is UNOS's ("marked") only with this GECOS, a uid in the range
# above and SMB_GROUP_GID as primary group. UNOS never touches other accounts.
SMB_GECOS = "UNOS SMB user"
SMB_SHELL = "/usr/sbin/nologin"
SMB_HOME = "/nonexistent"

# Names of SMB accounts and of new UNOS users. Always use fullmatch().
LINUX_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}\Z")
NAME_RULE = "1 to 32 characters: lowercase letters, digits, '_' and '-', starting with a letter"

RESERVED_ACCOUNT_NAMES = frozenset(
    {
        # Debian base-passwd users and groups
        "root", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail", "news",
        "uucp", "proxy", "www-data", "backup", "list", "irc", "gnats", "nobody", "nogroup",
        "adm", "tty", "disk", "kmem", "dialout", "fax", "voice", "cdrom", "floppy", "tape",
        "sudo", "audio", "dip", "operator", "src", "shadow", "utmp", "video", "sasl",
        "plugdev", "staff", "users", "netdev", "input", "kvm", "render", "sgx", "crontab",
        # services found on NAS hosts
        "messagebus", "sshd", "ssh", "polkitd", "avahi", "avahi-autoipd", "dnsmasq", "ntp",
        "ntpsec", "tss", "uuidd", "tcpdump", "lpadmin", "bluetooth", "i2c", "gpio", "spi",
        "docker", "sambashare", "postfix", "postdrop", "mysql", "postgres", "redis", "nginx",
        "statd", "rpc", "unbound", "usbmux", "pulse", "rtkit", "geoclue", "colord", "saned",
        # wsdd2.service, which install.sh sets up, runs as the dynamic user wsdd2: a static
        # account of that name, with its primary group, would take its place (ADR-0275).
        "wsdd2",
        # UNOS
        "unos", "unos-smb",
    }
)
RESERVED_NAME_PREFIXES = ("systemd-",)
# Samba matches an entry of valid users or write list that parses as a SID
# (S-1-1-0 is Everyone; its parser also takes lower case, hexadecimal and
# trailing text) against the SIDs of every session, so such a name would give
# its access to every SMB account. Names that start with 's-' and a digit are
# refused (use match(), not fullmatch()).
SID_LIKE_PREFIX = re.compile(r"s-[0-9]")
SID_LIKE_RULE = "Names that start with 's-' and a digit are reserved: Samba reads them as security identifiers (SIDs)"

SMB_PASSWORD_MIN_CHARS = 8
SMB_PASSWORD_MAX_BYTES = 72
# SMB has no second factor, and its lockout still lets 5 passwords per account be tried every
# 5 minutes (ADR-0334): passwords on the list of common_passwords.txt are refused (ADR-0279).
# The web UI shows its hint to change the password after every answer that starts with "SMB
# passwords must".
COMMON_PASSWORD_RULE = (
    "SMB passwords must not be common passwords: this one is on the list of passwords that attackers try first"
)
_COMMON_PASSWORDS_FILE = Path(__file__).with_name("common_passwords.txt")

SHARE_DIR_MODE = 0o2770   # share folders: group rwx + setgid, nothing for others
SHARE_FILE_MODE = 0o660   # files created over SMB
SHARE_EXECUTABLE_FILE_MODE = SHARE_FILE_MODE | 0o110  # files of a share that their owner may run


def share_entry_mode(mode: int) -> int | None:
    """The mode of an entry of a share as Repair permissions gives it (ADR-0363), and so Files to the
    copies it puts into a share folder (ADR-0396); None when it keeps its own.

    Folders 2770, regular files 0660, or 0770 when their owner may run them
    (the owner's execute bit). A private entry, which only its owner can use
    (no group or other bits, such as a 0600 key or a 0700 folder), keeps its
    mode, and so does whatever is neither a folder nor a regular file.
    """
    if not mode & 0o077:
        return None
    if stat.S_ISDIR(mode):
        return SHARE_DIR_MODE
    if stat.S_ISREG(mode):
        return SHARE_EXECUTABLE_FILE_MODE if mode & stat.S_IXUSR else SHARE_FILE_MODE
    return None


def linux_name_problem(name: object) -> str | None:
    """Why ``name`` cannot name a UNOS SMB account or a new UNOS user, or None."""
    if not isinstance(name, str) or not LINUX_NAME_PATTERN.fullmatch(name):
        return f"Use {NAME_RULE}"
    if name in RESERVED_ACCOUNT_NAMES or name.startswith(RESERVED_NAME_PREFIXES):
        return f"'{name}' is reserved for the system"
    if SID_LIKE_PREFIX.match(name):
        return SID_LIKE_RULE
    return None


def is_valid_linux_name(name: object) -> TypeGuard[str]:
    return linux_name_problem(name) is None


@functools.cache
def common_passwords() -> frozenset[str]:
    """The lines of common_passwords.txt that are not comments: casefolded passwords (ADR-0279)."""
    lines = _COMMON_PASSWORDS_FILE.read_text(encoding="utf-8").split("\n")
    return frozenset(line for line in lines if line and not line.startswith("#"))


def smb_password_problem(password: str, *, restoring: bool = False) -> str | None:
    """Why ``password`` cannot be given to Samba (smbpasswd reads it as a line on stdin), or None.

    The rules of ADR-0279, in this order: at least 8 characters, at most 72 bytes of valid UTF-8,
    no control characters, and not a common password (compared ignoring case). ``restoring``: the
    password that Samba held before a change that failed goes back to it (ADR-0124); the list
    applies only to new passwords, and an account may have got its password before the list.
    """
    if len(password) < SMB_PASSWORD_MIN_CHARS:
        return f"SMB passwords must have at least {SMB_PASSWORD_MIN_CHARS} characters"
    try:
        size = len(password.encode("utf-8"))
    except UnicodeEncodeError:
        return "SMB passwords must be valid Unicode text"
    if size > SMB_PASSWORD_MAX_BYTES:
        return f"SMB passwords must not be longer than {SMB_PASSWORD_MAX_BYTES} bytes (UTF-8)"
    if any(unicodedata.category(c) in ("Cc", "Cs") for c in password):
        return "SMB passwords must not contain control characters such as tabs or line breaks"
    if not restoring and password.casefold() in common_passwords():
        return COMMON_PASSWORD_RULE
    return None


def share_root_mode_ok(mode: int, gid: int) -> bool:
    """Whether a share's folder is set up for the group model (see Repair permissions)."""
    return (
        stat.S_ISDIR(mode)
        and gid == SMB_GROUP_GID
        and mode & 0o2070 == 0o2070
        and mode & 0o007 == 0
    )
