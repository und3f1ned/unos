#!/usr/bin/env python3
"""Scenario checks of the dev device, one area at a time (ADR-0304):

  scripts/dev/scenario.py AREA [--pool NAME]

Run it through scripts/dev/scenario.sh, which holds the device lock (ADR-0210) and reports the
SD writes of each check (ADR-0214). The areas:

  files    The File Manager on a pool: New Folder and Upload (755 and 644 root:root outside
           share folders), Download, Rename, Copy and Move; Delete into the pool's trash, Restore,
           a restore refused while the name is taken, Delete for good from the trash; a volume
           that Delete, Rename and Move refuse and Copy copies, a file deleted in the volume that
           goes into the volume's own trash, and a folder with a subvolume below it that Delete
           refuses.
  volumes  A volume made without a quota and listed, one with a quota of 1 GiB (refused while
           quotas are off on the pool, else made with that limit), a Delete refused while a share
           uses the volume; a snapshot of the volume, whose Restore makes the copy restore_<time>
           in it, a subvolume on the host listed with the volume's restored copies (ADR-0358), the
           copy's Delete refused while the share uses the copy and done once the share is gone,
           and, with the snapshot deleted, the volume's Delete.
  smart    The whole disks on the Storage page and on Disk Health, each disk's SMART data on both,
           the refusal of the system disk and of what is not a disk, and a short self-test of
           each disk.
  backups  A plain and an encrypted backup made and listed, the restores refused for a missing
           backup and for the encrypted one without its passphrase or with a wrong one, and both
           deleted. It never restores a backup.
  smb      A temporary user with SMB on and a temporary share on a new folder (the flows of
           slice 2, ADR-0185): the marked host account and its Samba account, a share list
           without shares, the folder's 2770 root:unos-smb, a signed session that writes, New
           Folder and Upload in the share's folder (2770 and 660 root:unos-smb) changed over SMB,
           the access lowered to read and then removed during open sessions, a comment saved with
           the expected version and a stale one refused; the protocol options (ADR-0377) of a
           second share, of a folder in a new volume: Previous Versions, which list a snapshot of
           the volume that the check makes on the Snapshots page with the name it gives as
           @GMT-<its time> for a file the user wrote and no longer once it is deleted, encryption
           required, which ends the share's open SMB 2.1 and unencrypted SMB3 sessions, refuses a
           new SMB 2.1 client and serves one that encrypts while the SMB 2.1 client still opens
           the first share, and with the option off the SMB 2.1 client again, and Time Machine
           with Previous Versions, whose lines and those of [global] testparm takes and whose
           change restarts avahi-daemon without a warning, after which Avahi resolves the share
           in the device's _adisk._tcp service, and no longer with the option off, each with the
           section's lines, then that share's and that volume's Delete; a rename of the
           user with a new password
           (ADR-0368) that ends the open session and keeps the marked host account's uid, and so
           the user's files, under the new name, with a Samba account of the new name and none of
           the old, the share's access and smb.conf's section following the new name, which signs
           in to UNOS and to the share while the old one with its old password is refused (by
           Samba only where the device has no guest share), a password change that ends the open
           session, the replaced password refused without a write to passdb.tdb, the new one
           taken, the user's own change of it over SAMR (smbpasswd -r, as Windows' Change a
           password asks for one) refused without a write (ADR-0342), a wrong password refused
           and counted in passdb.tdb, an anonymous session refused, 5 wrong passwords in a row
           that lock the account, so that the right one is refused too, without a write
           (ADR-0334), the [global] lines without a guest share and of Samba's log in
           /run/samba, no password of the check in an answer or a log, the share's Delete
           keeping its folder, and the user's Delete removing its accounts, still locked.
  smbconf  smb.conf edited by hand and what the Shares page says (ADR-0362): a share section
           added by hand (available = no) and UNOS's header removed, which the Shares page names
           before an apply; a new share whose apply copies that file byte for byte to a new
           smb.conf.unos-replaced-<time>, keeping the earlier copies, and makes smb.conf UNOS's
           again, and the copy and the earlier ones named on the Shares page; a comment saved with
           the expected version and a stale one refused; smb.conf edited by hand again, which the
           next comment's apply copies to a second copy, while the first keeps its bytes and is
           named on the Shares page as an earlier copy;
           the share's folder replaced by a symbolic link, so that the share is not served, with
           access refused without a new folder and saved with one; Delete keeping the folder with
           its files and smb.conf byte for byte as found; then, with the two copies deleted, a
           restart of the supervisor that writes no file in /etc/samba and leaves no problem.
  shares   The Shares and Account pages beyond slice 2 (ADR-0340): a sticky folder of root with a
           sticky folder "theirs" of another account in it, made over ssh on the pool, where a new share
           folder in "theirs" is refused with 409 and the reason, and a share of "theirs" itself
           is accepted with the warning that names its owner, reported on the Shares page and
           refused by Repair permissions (ADR-0193); a share on a new folder with entries of
           other groups and modes made over ssh, which Repair permissions, answered at once
           (202) running and waited for until the Shares API names its end (ADR-0363), gives the
           group unos-smb, group read and write and setgid on folders, keeping their owners, while a
           private file and folder, a symbolic link and the file outside the share that it
           points to stay as they were, and an ACL entry (where the device has setfacl) stays
           with the group bits as its mask (ADR-0363); the last run with its counts on the Shares
           API, and Repair refused while the share's folder is a symbolic link; a regular user
           without SMB, signed in with its own token, whose password of 7 characters (which
           UNOS's own rules allow) and one with a tab SMB refuses with the reason, while UNOS
           itself refuses one over 72 bytes (422), who turns SMB on with its password and no
           two-factor code, after an administrator allowed it, writes in a signed session, and
           turns SMB off again; guest access turned on for the share, with the answer's warning
           and the guest lines of [global], an anonymous session to the share that lists it and
           reads a file while a write is refused (what a null session's share list shows is only
           reported), and the Delete of that last guest share, after which signing is mandatory
           again (where the device has other guest shares, that step says so instead); no
           password of the check in an answer or a log; and the user's Delete.
  scrub    Scrubs of the pool (ADR-0314): its last scrub as found (none after a restart of the
           supervisor, ADR-0171); a scrub of a folder below the pools' base with nothing mounted
           on it refused with 409, and a scheduled scrub of that folder failing with the same
           text (ADR-0311); Start Scrub answered running, and so the status right after it
           (ADR-0313); the scrub reported, its bytes scrubbed growing, until it finished without
           errors; Cancel Scrub of a scrub that btrfs has recorded, which ends aborted; a disabled
           scheduled scrub task whose Run now starts a scrub ("Scrub started") that runs to its
           end; and the task deleted, its run left in the Scheduler's history. On a pool with more
           than 32 GiB of data the scrubs are cancelled instead of waited for. The pool's last
           scrub result is the check's afterwards.
  offsite  The off-device copy (ADR-0337), on a device where none is set up: a folder of the
           pool with two files as the source and another as the disk target; Save and test, which
           makes the copy repository there (700 root:root) and says that it lies on the disk it
           copies; Run now, whose copy, check and restore test pass; the snapshots of the
           configuration and of the folder; Restore configuration into the backup list as
           offsite_<snapshot time>.tar.gz, deleted again; Restore files into a new folder
           restored-<time> of the pool, compared byte for byte; no password of the check in an
           answer or a log; and Forget this target, which removes the settings, the password and
           the state. The clean-up waits for a copy or restore that still runs, and removes the
           check's folders, its restored folders and its offsite_ backups: while the check's
           target is the device's only one, no other copy or restore can make them.
  restore  Restores of a backup that the check makes by hand moments before (ADR-0341), on a
           device where no share that is not served has access, which a restore removes: two
           users with SMB on and a share with write access for both; the backup by hand and one
           that Run now of a disabled scheduled backup task makes, whose members (read in the
           supervisor's container) hold no smb.conf, the second deleted with its task; a restore
           with nothing changed since the backup, which keeps the users, the shares, the API
           tokens, the SMB host accounts, Samba's accounts and smb.conf byte for byte; then a new
           password for one user, which signs in, and a new user with SMB on and access to the
           share, and the same restore again: the users and the share as at the backup, smb.conf
           rendered from the restored database (only the first user in the share's section, and
           testparm takes it), the new user's host account and Samba account removed with its row
           (stale), the first user's password of the backup signing in a session that writes, the
           second user's two passwords refused, and SMB on again for it with a password set by an
           administrator under its uid; a user made after the restore gets a uid above the new
           user's, which the backup's next uid would have given again (ADR-0109); no password of
           the check in an answer or a log; the deletes. Each restore ends the device's sessions
           but the relay's (ADR-0329) and keeps a copy of the database from before it, which the
           clean-up removes once no restore can still run, unless a restore changed a user, a
           share, an API token of the relay's administrator or an account that is not the check's:
           then the copies stay, named by the clean-up's FAIL, for the owner. The check starts only
           when its two restores push out none of the copies that UNOS keeps (the newest 5). Where
           the device has a guest share, Samba takes a user without a Samba account as a guest
           (ADR-0339), so the refused sign-ins of the second user are left out.

Each check reads first how the device is (its baseline), and refuses to start beside leftovers
of an earlier check. It makes what it needs through the API, under names made of "unoscheck"
and the run's six hex digits (backups get UNOS's names), runs its steps in order and prints PASS
or FAIL for each with the MiB written to the SD card meanwhile; the first FAIL ends the steps.
Then it removes what it made, newest first, also after a FAIL or an interruption (Ctrl-C,
SIGTERM, SIGHUP; signals wait while the clean-up runs), and compares the device with the
baseline: a difference is a FAIL. Each clean-up is registered before the call that makes its
thing and finds the thing by its name, so a call whose answer was lost is undone as well; only a
backup whose answer was lost is named and left, since a backup made by hand meanwhile would look
the same. Exit status: 0 when every step passed, 1 when one failed, 2 for a usage error, a run
without the lock of scenario.sh or a dev.env that cannot be read, 130 after an interruption.

The API is called through api.py's relay (ADR-0222). The commands on the device run through ssh.sh,
in a private mount namespace with the root filesystem read-only (ADR-0221), with scratch files in
/dev/shm: stat, the account files' digests, testparm, Samba's log sizes, smbclient and the smb
check's smbpasswd -r with a client configuration of their own in RAM, the scan for passwords, the
shares check's entries on the pool (which the namespace leaves writable) and getfacl; only pdbedit
runs without that namespace, since tdbsam opens passdb.tdb read-write, and the smbconf check's edit
of smb.conf by hand, which writes /etc/samba (ADR-0309), the scrub check's folder below the pools'
base, on the root filesystem (ADR-0314), and the restore check's removal of the copies of the
database that its restores keep in the supervisor's data volume (ADR-0341). The restore check lists
a backup's members with python in the supervisor's running container (docker exec, as deploy.sh's
checks after up do, ADR-0183), which reads the backup in memory and writes nothing. The passwords of
the temporary users and the backups' passphrases are made here, go to the device only on standard
input (to the relay, into an auth file of mode 600 in RAM that smbclient reads, to smbpasswd -r and
to that python), and are never printed. The shares check's user signs in through the relay with a
token cache of its own in RAM (USER_TOKEN, mode 600 like the administrator's, ADR-0222), which its
clean-up deletes. The checks start no container (ADR-0183); the smbconf check restarts the
supervisor's (ADR-0309), and so does the restore check's clean-up when a restore left a host account
or Samba account that no row has, which the startup reconcile removes (ADR-0341).

Settings: dev.env (env.sh); UNOS_DEV_SHM, the RAM folder of the SMB helper on the device
(/dev/shm; the tests run it here). Standard library only; Python 3.9 or later.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import re
import secrets
import selectors
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import api as relay  # noqa: E402  scripts/dev/api.py

SSH = HERE / "ssh.sh"
SD = HERE / "sd.sh"
AREAS = ("files", "volumes", "smart", "backups", "smb", "smbconf", "shares", "scrub", "offsite", "restore")
MARK = "unoscheck"
LEFTOVER = re.compile(MARK + r"[0-9a-f]{6}")
POOL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
GIB = 1 << 30

# What the checks write and read back: the SMB helper makes data.txt and data2.txt with them.
DATA = "unos scenario check\n"
DATA2 = "unos scenario check, changed\n"
SMB_GROUP = "unos-smb"
SMB_GID = 70000
SMB_UIDS = range(70000, 80000)
# Samba's account policies as UNOS's startup reconcile sets them (src/shares/accounts.py, ADR-0278,
# ADR-0334; test_dev_scenarios.py compares): the key of REMOTE_SAMBA_USERS -> pdbedit's name and
# the value. An account is locked for LOCKOUT_MINUTES after LOCKOUT_ATTEMPTS failed sign-ins.
SAMBA_POLICIES = {
    "history": ("password history", 3),
    "duration": ("lockout duration", 5),
    "reset": ("reset count minutes", 5),
    "lockout": ("bad lockout attempt", 5),
}
LOCKOUT_ATTEMPTS = SAMBA_POLICIES["lockout"][1]
LOCKOUT_MINUTES = SAMBA_POLICIES["duration"][1]
ACCOUNT_FILES = ("/etc/passwd", "/etc/group", "/etc/shadow", "/etc/gshadow")
SMB_CONF = "/etc/samba/smb.conf"
SMB_HEADER = "# Generated by UNOS from its database."
SAMBA_LOGS = ("/var/log/samba/log.smbd", "/var/log/samba/log.nmbd")
SAMBA_RUN_LOG = "/run/samba/log.samba"
# The copies of an smb.conf with share sections that UNOS did not write, in /etc/samba, each named
# with the UTC time of the copy; an apply that makes one deletes the oldest beyond the newest
# REPLACED_COPIES_KEPT (src/shares/smb.py, ADR-0362; test_dev_scenarios.py compares).
REPLACED_COPY = "smb.conf.unos-replaced-<time>"
REPLACED_COPY_NAME = re.compile(r"smb\.conf\.unos-replaced-\d{8}_\d{6}_\d{6}")
REPLACED_COPIES_KEPT = 10
# What the Shares page's problem of a copy puts before the other copies (src/shares/service.py).
REPLACED_EARLIER = "; earlier copies (UNOS keeps the newest"
# What GET /shares/smb-status says of an smb.conf that is not UNOS's (ADR-0249), as
# src/shares/service.py words it (test_dev_scenarios.py compares).
CONF_NOT_UNOS = "smb.conf was replaced or removed after UNOS wrote it"
# The fixed lines that end every share section UNOS renders (src/shares/smbconf.py; ADR-0147,
# ADR-0150): a share section without them is one that UNOS did not write (ADR-0362).
UNOS_SHARE_LINES = (("veto files", "/.snapshots/.unos-trash/"), ("force group", "unos-smb"), ("create mask", "0660"),
                    ("force create mode", "0660"), ("directory mask", "2770"), ("force directory mode", "2770"))
NOT_SHARES = ("global", "homes", "printers", "print$")
# The start of the guest warning in the answer of a change that turns guest access on
# (GUEST_WARNING of src/shares/service.py, ADR-0096; test_dev_scenarios.py compares).
GUEST_WARNS = "Anyone on your network can read this share without a password"
# The [global] lines that follow whether a guest share exists (ADR-0339, ADR-0100): without one,
# with one.
GUEST_GLOBALS = {"map to guest": ("Never", "Bad User"), "restrict anonymous": ("2", "1"),
                 "server signing": ("mandatory", "default")}
# Why SMB refuses a password (smb_password_problem of src/shares/constants.py, ADR-0279) under 8
# characters, and with a control character; UNOS itself refuses a new password over 72 bytes
# (MAX_PASSWORD_BYTES of src/auth/schemas.py, ADR-0315) with 422.
SMB_TOO_SHORT = "SMB passwords must have at least 8 characters"
SMB_CONTROL = "SMB passwords must not contain control characters such as tabs or line breaks"
UNOS_PASSWORD_BYTES = 72
# What smbpasswd -r prints for the NT status of a password change that the server refused, for the
# statuses a refused change carries: Samba's description of the status (get_friendly_nt_error_msg
# of libcli/util/nterr.c), or its name when it has none. Samba 4.22 prints MS-ERREF's description
# (libcli/util/ntstatus_err_table.txt) after "rejected to change the password with error: ", and
# this names each by a phrase of it; older releases print a short text of Samba's own table after
# "Error was : ". Read without regard to case. The check password script of UNOS's [global] refuses
# with the first (ADR-0342).
FRIENDLY_NT_STATUS = (
    ("some password update rule has been violated", "NT_STATUS_PASSWORD_RESTRICTION"),
    ("value provided as the current password is not correct", "NT_STATUS_WRONG_PASSWORD"),
    ("automatically locked because too many invalid logon", "NT_STATUS_ACCOUNT_LOCKED_OUT"),
    ("some user account restriction has prevented", "NT_STATUS_ACCOUNT_RESTRICTION"),
    ("The attempted logon is invalid", "NT_STATUS_LOGON_FAILURE"),
    ("{Access Denied}", "NT_STATUS_ACCESS_DENIED"),
    ("Error was : Password restriction.", "NT_STATUS_PASSWORD_RESTRICTION"),
    ("Error was : Wrong Password.", "NT_STATUS_WRONG_PASSWORD"),
    ("Error was : Account locked out.", "NT_STATUS_ACCOUNT_LOCKED_OUT"),
    ("Error was : Account restriction.", "NT_STATUS_ACCOUNT_RESTRICTION"),
    ("Error was : Logon failure.", "NT_STATUS_LOGON_FAILURE"),
    ("Error was : Access denied.", "NT_STATUS_ACCESS_DENIED"),
)
# The token cache in the device's RAM of the shares check's user (api.py's relay, ADR-0222).
USER_TOKEN = "/dev/shm/unos-scenario-{}.token"
# What REMOTE_ENTRIES repair makes in the shares check's share folder, and how Repair permissions
# leaves it (ADR-0155, ADR-0156): name -> (mode and owner as made, after the repair), where
# {user} and {group} are the other account's. Owners never change; private entries, which only
# their owner can use, and link.txt, a symbolic link to <folder>-outside/target.txt, stay.
REPAIR_ENTRIES = {
    "wrong": ("755 root:root", "2770 root:unos-smb"),
    "wrong/inner.txt": ("644 root:root", "660 root:unos-smb"),
    "run.sh": ("750 root:root", "770 root:unos-smb"),
    "legacy.txt": ("666 {user}:{group}", "660 {user}:unos-smb"),
    "acl.txt": ("640 root:root", "660 root:unos-smb"),
    "private.txt": ("600 root:root", "600 root:root"),
    "private": ("700 root:root", "700 root:root"),
    "private/inner.txt": ("644 root:root", "644 root:root"),
    "link.txt": ("777 root:root", "777 root:root"),
}
# The entries that the repair visits besides the share's folder (it does not enter private/), and
# those it changes.
REPAIR_VISITS = len(REPAIR_ENTRIES) - 1
REPAIR_CHANGES = sum(1 for made, after in REPAIR_ENTRIES.values() if made != after)

# The read-only root of env.sh's DEV_RO_ROOT (ADR-0221), for every command but pdbedit.
RO_ROOT = 'mount -o remount,bind,ro,noatime / || { echo "could not make / read-only in a private mount namespace" >&2; exit 1; }'


# --- the scripts that run on the device ------------------------------------------------------

# $@: paths. Prints "<path>\t<mode> <owner>:<group>" or "<path>\tmissing" for each.
REMOTE_STAT = r'''
for p in "$@"; do
    s=$(stat -c "%a %U:%G" -- "$p" 2> /dev/null) || s=missing
    printf "%s\t%s\n" "$p" "$s"
done'''

# $1: a file; prints "file <its bytes in base64>", or "missing".
REMOTE_READ = r'''
if [ -f "$1" ]; then echo "file $(base64 < "$1" | tr -d "\n")"; else echo missing; fi'''

# The facts of the SMB check: the device's time, the tools, the digests of the account files and
# smb.conf (compared, never printed), the marked accounts' passwd lines, the group unos-smb,
# testparm on smb.conf, passdb.tdb's time of change and the sizes of Samba's logs.
REMOTE_FACTS = r'''
set -uo pipefail
echo "now $(date +%s)"
for t in getent getfacl pdbedit setfacl smbclient smbpasswd stdbuf testparm; do
    if command -v "$t" > /dev/null 2>&1; then echo "tool $t"; fi
done
for f in /etc/passwd /etc/group /etc/shadow /etc/gshadow /etc/samba/smb.conf; do
    echo "sha256 $f $(sha256sum < "$f" 2> /dev/null | cut -d " " -f 1)"
done
getent passwd | awk -F: '$3 >= 70000 && $3 <= 79999 { print "account " $0 }'
echo "group $(getent group unos-smb)"
mkdir -p -m 0755 /run/samba
if testparm -s /etc/samba/smb.conf > /dev/null 2>&1; then echo "testparm ok"; else echo "testparm failed"; fi
echo "passdb $(stat -c %y /var/lib/samba/private/passdb.tdb 2> /dev/null || echo missing)"
for f in /var/log/samba/log.smbd /var/log/samba/log.nmbd /run/samba/log.samba; do
    echo "size $f $(stat -c %s "$f" 2> /dev/null || echo missing)"
done'''

# Without the read-only namespace: tdbsam opens passdb.tdb (and account_policy.tdb) read-write,
# which a read-only root refuses. Only lists and reads: the account policies of SAMBA_POLICIES.
REMOTE_SAMBA_USERS = r'''
set -uo pipefail
pdbedit -L 2> /dev/null | cut -d : -f 1 | sed "s/^/user /"
echo "history $(pdbedit -P "password history" 2> /dev/null | tail -n 1)"
echo "duration $(pdbedit -P "lockout duration" 2> /dev/null | tail -n 1)"
echo "reset $(pdbedit -P "reset count minutes" 2> /dev/null | tail -n 1)"
echo "lockout $(pdbedit -P "bad lockout attempt" 2> /dev/null | tail -n 1)"'''

# $1: create, delete or show; $2: a subvolume of a scenario check's folder (show: any path below
# /mnt/pools). The pool is not the root filesystem, so the read-only namespace leaves it writable.
REMOTE_SUBVOLUME = r'''
set -uo pipefail
case $1:$2 in
    *..* | *//*) echo "not a subvolume of a scenario check: $1 $2" >&2; exit 2 ;;
    show:/mnt/pools/*) ;;
    create:/mnt/pools/*/unoscheck[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]/?* | delete:/mnt/pools/*/unoscheck[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]/?*) ;;
    *) echo "not a subvolume of a scenario check: $1 $2" >&2; exit 2 ;;
esac
case $1 in
    create) btrfs subvolume create "$2" > /dev/null || exit 1 ;;
    delete) if [ -e "$2" ]; then btrfs subvolume delete "$2" > /dev/null || exit 1; fi ;;
esac
if btrfs subvolume show "$2" > /dev/null 2>&1; then echo subvolume; else echo none; fi'''

# The disk that holds the root filesystem.
REMOTE_SYSTEM_DISK = r'''
lsblk -no PKNAME "$(findmnt -no SOURCE /)"'''

# smbd's Time Machine announcement (ADR-0377): Avahi resolves the device's _adisk._tcp service over
# D-Bus (busctl ships with systemd) under the name smbd gives its services, the netbios name (mdns
# name = netbios, Samba's default). Prints "found <busctl's JSON>", or "none <busctl's error>" when the
# call fails; adisk_names tells Avahi's answer for no such service from a call that could not ask it.
REMOTE_ADISK = r'''
set -uo pipefail
mkdir -p -m 0755 /run/samba
name=$(testparm -s --parameter-name "netbios name" 2> /dev/null) || name=
if [ -z "$name" ]; then echo "testparm names no netbios name" >&2; exit 1; fi
if out=$(busctl --json=short -- call org.freedesktop.Avahi / org.freedesktop.Avahi.Server ResolveService \
        iisssiu -1 -1 "$name" _adisk._tcp local -1 0 2>&1); then
    printf "found %s\n" "$out"
else
    printf "none %s\n" "$(printf "%s" "$out" | tr "\n" " ")"
fi'''

# The device's time in seconds, since which the scan reads the logs.
REMOTE_NOW = r'''
date +%s'''

# $1: a volume. The limit (max referenced, bytes) of its quota group, or "none".
REMOTE_QUOTA = r'''
set -uo pipefail
id=$(btrfs subvolume show "$1" 2> /dev/null | awk '$1 == "Subvolume" && $2 == "ID:" { print $3; exit }')
if [ -z "$id" ]; then echo none; exit 0; fi
btrfs qgroup show -re --raw "$1" 2> /dev/null | awk -v q="0/$id" '$1 == q { print $4; found = 1 } END { if (!found) print "none" }'
'''

# $1: the device's time (seconds) since which the logs are read. Standard input: the passwords,
# one per line, which go into a file of mode 600 in RAM and are deleted. Prints how many lines
# of the supervisor's log, the journal, Samba's log and the audit log hold one, and -1 for a
# source that could not be read.
REMOTE_SCAN = r'''
set -uo pipefail
umask 077
dir=$(mktemp -d "${UNOS_DEV_SHM:-/dev/shm}/unos-scenario-scan.XXXXXX") || exit 1
trap 'rm -rf "$dir"' EXIT
cat > "$dir/needles"
count() { grep -c -F -f "$dir/needles" || true; }
if n=$(docker logs --since "$1" unos-supervisor 2>&1 | count); then echo "supervisor $n"; else echo "supervisor -1"; fi
if n=$(journalctl --since "@$1" --no-pager -q 2> /dev/null | count); then echo "journal $n"; else echo "journal -1"; fi
if [ -r /run/samba/log.samba ]; then echo "samba $(count < /run/samba/log.samba)"; else echo "samba -1"; fi
data=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' unos-supervisor 2> /dev/null) || data=
if [ -n "$data" ] && [ -r "$data/audit/audit.jsonl" ]; then
    echo "audit $(cat "$data"/audit/audit.jsonl* | count)"
else
    echo "audit -1"
fi'''

# smbclient on the device, against its own smbd (127.0.0.1, which hosts allow admits). $1: list
# (a share list, -L), anonymous (the same as a null session: no user, no password, and no
# signing required by the client, so that a refusal can only come from the server), session, or
# guest (a session to a share as a null session, as anonymous signs in); $2: the user; $3: the
# share; $4 (session): smb2, a client of SMB 2.1 at most, which cannot encrypt, or encrypt, a
# client that requires SMB3 encryption (ADR-0377). The first line of standard input is the
# password: it goes into an auth file of mode 600 in a private folder in RAM, deleted at the end,
# with a client configuration that requires signing (client signing for a share, client ipc
# signing for the IPC$ of a share list) and keeps smbclient's own files there. list and anonymous print "end <status> <output in
# base64>". session and guest read commands from standard input and pass them to one smbclient
# session; "pause N" waits until smbclient has run what came before and prints "at N <its
# output>", or "ended N <output>" when smbclient has ended; after the last line, "end <status>
# <output>".
REMOTE_SMB = r'''
set -uo pipefail
trap "" PIPE
mode=$1 user=$2 share=$3 protection=${4:-}
umask 077
dir=$(mktemp -d "${UNOS_DEV_SHM:-/dev/shm}/unos-scenario-smb.XXXXXX") || exit 1
pid=
trap '[ -z "$pid" ] || kill "$pid" 2> /dev/null; rm -rf "$dir"' EXIT
cd "$dir" || exit 1
IFS= read -r password || password=
signing=required
case $mode in anonymous | guest) signing=default ;; esac
{
    echo "[global]"
    echo "   client min protocol = SMB2_02"
    echo "   client signing = $signing"
    echo "   client ipc signing = $signing"
    case $protection in
        smb2) echo "   client max protocol = SMB2_10" ;;
        encrypt) echo "   client smb encrypt = required" ;;
    esac
    for d in "cache directory" "lock directory" "state directory" "private dir" "pid directory" "ncalrpc dir"; do
        echo "   $d = $dir"
    done
    echo "   log level = 0"
} > smb.conf
if [ "$mode" = change ]; then
    # A change of the user's password over SAMR, as Windows' Change a password asks for one
    # (ADR-0342): the old password, then the new one twice, on smbpasswd's standard input, from
    # printf, a builtin, so that no command line holds them; the exit status is smbpasswd's.
    IFS= read -r new || new=
    rc=0
    out=$(set +o pipefail; printf "%s\n%s\n%s\n" "$password" "$new" "$new" |
        smbpasswd -c "$dir/smb.conf" -r 127.0.0.1 -s -U "$user" 2>&1) || rc=$?
    password= new=
    echo "end $rc $(printf "%s" "$out" | base64 | tr -d "\n")"
    exit 0
fi
if [ "$mode" = anonymous ] || [ "$mode" = guest ]; then
    auth=(-N -U%)
else
    printf "username = %s\npassword = %s\n" "$user" "$password" > auth
    auth=(-A "$dir/auth")
fi
password=
printf "unos scenario check\n" > data.txt
printf "unos scenario check, changed\n" > data2.txt
b64() { base64 | tr -d "\n"; }
if [ "$mode" = list ] || [ "$mode" = anonymous ]; then
    rc=0
    out=$(smbclient -s "$dir/smb.conf" "${auth[@]}" -g -L //127.0.0.1 < /dev/null 2>&1) || rc=$?
    echo "end $rc $(printf "%s" "$out" | b64)"
    exit 0
fi
mkfifo in
# Unbuffered: smbclient waits for input with poll on its descriptor and then reads a line with
# fgets, so a line in stdio's buffer would wait unseen; its output reaches "out" at once.
stdbuf -i0 -o0 -e0 smbclient -s "$dir/smb.conf" "${auth[@]}" "//127.0.0.1/$share" < in > out 2>&1 &
pid=$!
exec 3> in
sent=0
# emit KIND N: a line with the output since the last emit.
emit() {
    local size
    size=$(wc -c < out | tr -d " ")
    echo "$1 $2 $(tail -c +"$((sent + 1))" out | head -c "$((size - sent))" | b64)"
    sent=$size
}
while IFS= read -r line; do
    case $line in
        "pause "*)
            n=${line#pause }
            mark="@@unos-scenario-$n@@"
            echo "!echo $mark" >&3
            found=
            for _ in $(seq 1 600); do
                if grep -qF "$mark" out; then found=1; break; fi
                kill -0 "$pid" 2> /dev/null || break
                sleep 0.1
            done
            if [ -n "$found" ]; then emit at "$n"; else emit ended "$n"; fi
            ;;
        *) echo "$line" >&3 ;;
    esac
done
exec 3>&-
rc=0
wait "$pid" || rc=$?
pid=
emit end "$rc"'''

# The files of /etc/samba: "file <name> <sha256> <time of change> <mode> <owner>:<group>" each.
REMOTE_SAMBA_FILES = r'''
set -uo pipefail
cd "${UNOS_DEV_SAMBA:-/etc/samba}" || exit 1
for f in *; do
    [ -f "$f" ] || continue
    printf "file %s %s %s %s\n" "$f" "$(sha256sum < "$f" | cut -d " " -f 1)" "$(stat -c %Y -- "$f")" "$(stat -c "%a %U:%G" -- "$f")"
done'''

# The smbconf check's edit of smb.conf by hand (ADR-0362), without the read-only namespace: it
# writes /etc/samba. $1: edit, again or restore; $2: the check's section, unoscheck<6 hex
# digits>hand; $3 (edit, again): smb.conf's digest as the check read it. edit keeps smb.conf in
# RAM and rewrites it in place, as an editor that keeps the file's inode, owner and mode: UNOS's
# header replaced by a comment and the section appended, which Samba does not serve (available =
# no); again does the same to smb.conf as UNOS's apply left it, and keeps the first edit's
# original in RAM. Each refuses when smb.conf changed since the check read it or when /etc/samba
# keeps so many copies smb.conf.unos-replaced-<time> that the check's copies would make UNOS
# delete the oldest: 1 less than REPLACED_COPIES_KEPT for edit, whose two applies each make one,
# and REPLACED_COPIES_KEPT for again, with the first among them. restore ($3...: the digests of
# the check's edited files, or none) puts the kept smb.conf back when smb.conf does not start
# with UNOS's header (after an apply it holds UNOS's newer rendering, which stays), deletes the
# copy in RAM, and deletes the copies that hold exactly one of the check's edited files. Prints
# "conf <digest>" and "copy <name> <digest>" for each copy.
REMOTE_HAND_EDIT = r'''
set -uo pipefail
action=$1 section=$2
shift 2
if ! [[ $section =~ ^unoscheck[0-9a-f]{6}hand$ ]]; then
    echo "not a section of a scenario check: $section" >&2
    exit 2
fi
dir=${UNOS_DEV_SAMBA:-/etc/samba}
conf=$dir/smb.conf
kept=''' + str(REPLACED_COPIES_KEPT) + r'''
saved=${UNOS_DEV_SHM:-/dev/shm}/unos-scenario-$section.conf
header="# Generated by UNOS from its database."
digest() { sha256sum < "$1" | cut -d " " -f 1; }
copies() {
    local f
    for f in "$dir"/smb.conf.unos-replaced-*; do
        if [[ ${f##*/} =~ ^smb\.conf\.unos-replaced-[0-9]{8}_[0-9]{6}_[0-9]{6}$ ]] && [ -f "$f" ] && [ ! -L "$f" ]; then
            echo "$f"
        fi
    done
}
case $action in
    edit|again)
        [ "$(digest "$conf")" = "${1:-}" ] || { echo "smb.conf changed since the check read it" >&2; exit 1; }
        if [ "$action" = edit ]; then
            [ ! -e "$saved" ] || { echo "$saved exists" >&2; exit 1; }
            limit=$((kept - 1))
        else
            [ -f "$saved" ] || { echo "the first edit's original $saved is missing" >&2; exit 1; }
            limit=$kept
        fi
        count=0
        while IFS= read -r f; do count=$((count + 1)); done < <(copies)
        [ "$count" -lt "$limit" ] || { echo "$dir keeps $count copies smb.conf.unos-replaced-<time>: the check's copies would make UNOS delete the oldest" >&2; exit 1; }
        if [ "$action" = edit ]; then cp -p "$conf" "$saved" || exit 1; fi
        {
            echo "# Edited by hand for a scenario check of UNOS" &&
            awk 'body || !/^#/ { body = 1; print }' "$conf" &&
            printf "\n[%s]\n   path = /srv/%s\n   available = no\n" "$section" "$section"
        } > "$saved.new" || { [ "$action" = again ] || rm -f "$saved"; rm -f "$saved.new"; exit 1; }
        # A write that fails half-way leaves smb.conf without UNOS's header: restore puts it back.
        cat "$saved.new" > "$conf" || { rm -f "$saved.new"; exit 1; }
        rm -f "$saved.new"
        ;;
    restore)
        if [ -f "$saved" ] && [ "$(head -c "${#header}" "$conf" 2> /dev/null)" != "$header" ]; then
            cat "$saved" > "$conf" || exit 1
        fi
        rm -f "$saved"
        if [ "$#" -gt 0 ]; then
            while IFS= read -r f; do
                d=$(digest "$f")
                for e in "$@"; do
                    if [ "$d" = "$e" ]; then rm -f -- "$f" || exit 1; break; fi
                done
            done < <(copies)
        fi
        ;;
    *) echo "not an action: $action" >&2; exit 2 ;;
esac
echo "conf $(digest "$conf")"
while IFS= read -r f; do echo "copy ${f##*/} $(digest "$f")"; done < <(copies)'''

# $1: link or restore; $2: a share folder of a scenario check, <pools>/<pool>/unoscheck<6 hex
# digits>. link moves the folder to <folder>-moved and puts a symbolic link to it in its place,
# so that UNOS no longer serves the share (ADR-0145); restore removes the link and moves the
# folder back. On the pool, which the read-only namespace leaves writable. Prints link, folder or
# missing: what the path is now.
REMOTE_UNSERVE = r'''
set -uo pipefail
action=$1 path=$2
base=${UNOS_DEV_POOLS:-/mnt/pools}
rest=${path#"$base"/}
if [ "$rest" = "$path" ] || ! [[ $rest =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}/unoscheck[0-9a-f]{6}$ ]]; then
    echo "not a share folder of a scenario check: $path" >&2
    exit 2
fi
moved=$path-moved
case $action in
    link)
        if [ -L "$path" ] || [ ! -d "$path" ] || [ -e "$moved" ] || [ -L "$moved" ]; then
            echo "$path is not a plain folder, or $moved exists" >&2
            exit 1
        fi
        mv -- "$path" "$moved" && ln -s -- "${moved##*/}" "$path" || exit 1
        ;;
    restore)
        if [ -L "$path" ]; then rm -f -- "$path" || exit 1; fi
        if [ -d "$moved" ] && [ ! -L "$moved" ] && [ ! -e "$path" ]; then mv -- "$moved" "$path" || exit 1; fi
        ;;
    *) echo "not an action: $action" >&2; exit 2 ;;
esac
if [ -L "$path" ]; then echo link; elif [ -d "$path" ]; then echo folder; else echo missing; fi'''

# The shares check's entries on the pool, which the read-only namespace leaves writable. $1:
# repair, sticky or acl. repair ($2: a share folder of a scenario check, <pools>/<pool>/unoscheck<6
# hex digits>; $3: acl or noacl) makes REPAIR_ENTRIES in it, with these owners and modes, and
# <folder>-outside/target.txt (644 root:root), to which link.txt points: entries made in the
# share's setgid folder get its group, so each one gets its own owner, group and mode here; with
# acl, acl.txt gets an ACL entry that lets the other account read it. sticky ($2: <that
# folder>-sticky) makes the folder 1777 root:root and in it the folder theirs, 1777 of the other
# account. Both refuse a folder that exists and print "account <user>:<group> <uid>" of the other
# account, UNOS_DEV_OTHER_ACCOUNT (nobody:nogroup). acl ($2: an entry of such a share folder) only
# reads: "acl <entry>" for each entry of the path's ACL (getfacl).
REMOTE_ENTRIES = r'''
set -uo pipefail
action=$1 path=$2 acl=${3:-noacl}
base=${UNOS_DEV_POOLS:-/mnt/pools}
other=${UNOS_DEV_OTHER_ACCOUNT:-nobody:nogroup}
folder='[A-Za-z0-9][A-Za-z0-9_-]{0,63}/unoscheck[0-9a-f]{6}'
case $action in
    repair) pattern="^$folder\$" ;;
    sticky) pattern="^$folder-sticky\$" ;;
    acl) pattern="^$folder/[A-Za-z0-9_-]+(\\.[A-Za-z0-9]+)?\$" ;;
    *) echo "not an action: $action" >&2; exit 2 ;;
esac
rest=${path#"$base"/}
if [ "$rest" = "$path" ] || ! [[ $rest =~ $pattern ]]; then
    echo "not a folder of a scenario check: $path" >&2
    exit 2
fi
if [ "$action" = acl ]; then
    out=$(getfacl -c -E -- "$path") || exit 1
    printf "%s\n" "$out" | sed -n "s/^\([a-z]\)/acl \1/p"
    exit 0
fi
user=${other%%:*}
uid=$(id -u "$user") || exit 1
umask 022
if [ "$action" = sticky ]; then
    # theirs gets its mode while its parent is still 755 root:root: in the open sticky folder its
    # owner could put a symbolic link in its place, which chmod would follow.
    mkdir -- "$path" && mkdir -- "$path/theirs" &&
        chown root:root "$path" && chown "$other" "$path/theirs" && chmod 01777 "$path/theirs" &&
        chmod 01777 "$path" || exit 1
    echo "account $other $uid"
    exit 0
fi
outside=$path-outside
if [ -L "$path" ] || [ ! -d "$path" ] || [ -e "$outside" ] || [ -L "$outside" ]; then
    echo "$path is not a plain folder, or $outside exists" >&2
    exit 1
fi
cd -- "$path" || exit 1
for name in wrong private run.sh legacy.txt private.txt acl.txt link.txt; do
    if [ -e "$name" ] || [ -L "$name" ]; then echo "$path/$name exists" >&2; exit 1; fi
done
write() { printf "unos scenario check\n" > "$1"; }
mkdir -- "$outside" && write "$outside/target.txt" &&
    mkdir wrong private && write wrong/inner.txt && write private/inner.txt &&
    write run.sh && write legacy.txt && write private.txt && write acl.txt &&
    ln -s -- "../${outside##*/}/target.txt" link.txt &&
    chown -h root:root "$outside" "$outside/target.txt" wrong wrong/inner.txt private private/inner.txt \
        run.sh private.txt acl.txt link.txt &&
    chown "$other" legacy.txt &&
    chmod 00755 "$outside" wrong && chmod 00700 private &&
    chmod 644 "$outside/target.txt" wrong/inner.txt private/inner.txt &&
    chmod 750 run.sh && chmod 666 legacy.txt && chmod 600 private.txt && chmod 640 acl.txt || exit 1
if [ "$acl" = acl ]; then
    setfacl -m "u:$user:r" acl.txt || exit 1
fi
echo "account $other $uid"'''

# A folder of the check below the pools' base, where nothing is mounted (ADR-0311): on the root
# filesystem, so it runs without the read-only namespace. $1: make, remove or list; $2: the
# folder's name, unoscheck and six hex digits. Prints the folders of the base, one per line.
REMOTE_POOL_FOLDER = r'''
set -uo pipefail
action=$1 name=${2:-}
base=${UNOS_DEV_POOLS:-/mnt/pools}
case $action in
    make | remove)
        if ! [[ $name =~ ^unoscheck[0-9a-f]{6}$ ]]; then
            echo "not a folder of a scenario check: $name" >&2
            exit 2
        fi
        path=$base/$name
        if [ "$action" = make ]; then
            mkdir -m 0755 -- "$path" || exit 1
        elif [ -d "$path" ] && [ ! -L "$path" ] && ! mountpoint -q -- "$path"; then
            rmdir -- "$path" || exit 1
        fi
        ;;
    list) ;;
    *) echo "not an action: $action" >&2; exit 2 ;;
esac
ls -1A -- "$base"'''

# The supervisor's restart (ADR-0309): Docker restarts the container that runs (no new one,
# ADR-0183); prints "healthy <seconds>" once its health check passes again.
REMOTE_RESTART = r'''
set -uo pipefail
docker restart unos-supervisor > /dev/null || exit 1
for i in $(seq 1 90); do
    sleep 2
    s=$(docker inspect -f '{{.State.Health.Status}}' unos-supervisor 2> /dev/null) || s=
    if [ "$s" = healthy ]; then echo "healthy $((i * 2))"; exit 0; fi
done
echo "unos-supervisor is not healthy 180 seconds after its restart" >&2
exit 1'''

# The members of a backup (the restore check, ADR-0341), read by python in the supervisor's
# container, which has the backup and the cryptography package: argv: the backups' folder and the
# backup's name; for an encrypted one (.tar.gz.enc), the first line of standard input is its
# passphrase. The file is read into memory and decrypted there as src/backup/router.py writes it
# (salt, nonce, AES-256-GCM; the key from PBKDF2 with SHA-256; test_dev_scenarios.py opens files
# that router.py encrypts), and nothing is written (-B). Prints "member <name>" for each member.
ARCHIVE_MEMBERS = r'''
import hashlib, io, sys, tarfile
folder, name = sys.argv[1:3]
with open(folder + "/" + name, "rb") as f:
    data = f.read()
if name.endswith(".enc"):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    passphrase = sys.stdin.readline().rstrip("\n").encode()
    key = hashlib.pbkdf2_hmac("sha256", passphrase, data[:16], 100000, 32)
    data = AESGCM(key).decrypt(data[16:28], data[28:], None)
with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
    for member in tar:
        print("member", member.name)'''

# $1: the name of a backup in the supervisor's /data/backups. Runs ARCHIVE_MEMBERS there with
# docker exec (no new container, ADR-0183), passing standard input on; UNOS_DEV_BACKUPS names
# another folder of the container (the tests run it here).
REMOTE_ARCHIVE = r'''
set -uo pipefail
name=$1
if ! [[ $name =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.tar\.gz(\.enc)?$ ]]; then
    echo "not the name of a backup: $name" >&2
    exit 2
fi
IFS= read -r -d '' py << 'PY'
''' + ARCHIVE_MEMBERS.strip("\n") + r'''
PY
docker exec -i unos-supervisor python -I -B -c "$py" "${UNOS_DEV_BACKUPS:-/data/backups}" "$name"'''

# The copies of the database that a restore keeps next to it, unos.db.pre-restore-<time>, of which
# src/backup/router.py keeps the newest PRE_RESTORE_COPIES (the restore check, ADR-0341). $1: list,
# or remove with the names of copies after it, which runs without the read-only namespace: the
# supervisor's data volume is on the root filesystem. Removes only names of such copies; prints
# "copy <name>" for each copy afterwards. UNOS_DEV_DATA names the folder (the tests run it here).
REMOTE_DB_COPIES = r'''
set -uo pipefail
action=$1
shift
data=${UNOS_DEV_DATA:-}
if [ -z "$data" ]; then
    data=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' unos-supervisor) || data=
fi
[ -n "$data" ] && [ -d "$data" ] || { echo "the supervisor's data volume was not found" >&2; exit 1; }
case $action in
    list) ;;
    remove)
        for name in "$@"; do
            if ! [[ $name =~ ^unos\.db\.pre-restore-[0-9]{8}_[0-9]{6}_[0-9]{6}$ ]]; then
                echo "not a copy of the database from before a restore: $name" >&2
                exit 2
            fi
        done
        for name in "$@"; do rm -f -- "$data/$name" || exit 1; done
        ;;
    *) echo "not an action: $action" >&2; exit 2 ;;
esac
for f in "$data"/unos.db.pre-restore-*; do
    if [ -f "$f" ] && [ ! -L "$f" ]; then echo "copy ${f##*/}"; fi
done'''


# --- results ---------------------------------------------------------------------------------


class Failed(Exception):
    """A step's failure, with what went wrong."""


class Stop(Exception):
    """Ends the steps of a check after a FAIL."""


def b64text(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def unb64(text: str) -> str:
    return base64.b64decode(text.encode()).decode("utf-8", "replace") if text else ""


def secret_forms(secret: str) -> set[str]:
    """SECRET as it may be printed: as is, as JSON writes it (a tab as \\t) and as tail() joins
    its whitespace (a tab as a space)."""
    return {form for form in (secret, json.dumps(secret)[1:-1], " ".join(secret.split())) if form}


def gmt_token(snapshot: str) -> str | None:
    """The @GMT token under which Previous Versions list a snapshot that UNOS named (snap_ or
    auto_<UTC time>, ADR-0243), as shadow_copy2 reads it with UNOS's shadow:format (ADR-0377);
    None for another name."""
    found = re.fullmatch(r"(?:snap|auto)_(\d{4})(\d\d)(\d\d)_(\d\d)(\d\d)(\d\d)", snapshot)
    return "@GMT-{}.{}.{}-{}.{}.{}".format(*found.groups()) if found else None


# smbd registers its _adisk._tcp service a moment after avahi-daemon runs again.
ADISK_WAIT = 30
ADISK_POLL = 2


def adisk_until(c: Check, share: str, *, listed: bool) -> list[str]:
    """The shares of the device's _adisk._tcp service (smbd's Time Machine announcement, ADR-0377) once
    SHARE is among them (LISTED) or not, within ADISK_WAIT seconds."""
    deadline = time.monotonic() + ADISK_WAIT
    while True:
        names = c.device.adisk_shares()
        if (share.lower() in [name.lower() for name in names or []]) == listed:
            return names or []
        found = f"it names {', '.join(names)}" if names else "Avahi resolves no such service"
        c.expect(time.monotonic() < deadline,
                 f"Avahi does not resolve {share} in the device's _adisk._tcp service within {ADISK_WAIT} s ({found})"
                 if listed else f"Avahi still resolves {share} in the device's _adisk._tcp service after "
                                f"{ADISK_WAIT} s ({found})")
        time.sleep(ADISK_POLL)


# Avahi's answer when it resolves no such service: its error org.freedesktop.Avahi.TimeoutError,
# whose message busctl prints.
AVAHI_NOT_FOUND = ("Timeout reached", "org.freedesktop.Avahi.TimeoutError")


def adisk_names(out: str) -> list[str] | None:
    """The shares that the device's _adisk._tcp service names (TXT records dk<n>=adVN=<share>,...),
    from REMOTE_ADISK's output; None when Avahi answers that it resolves no such service. Any other
    failure of the call (avahi-daemon not on the bus, a D-Bus denial, no busctl) fails with its text,
    so that a check of Time Machine turned off does not pass without asking Avahi."""
    kind, _, rest = out.strip().partition(" ")
    if kind == "none":
        if any(answer in rest for answer in AVAHI_NOT_FOUND):
            return None
        raise Failed(f"Avahi could not be asked for the device's _adisk._tcp service: {tail(rest, 200)}")
    try:
        if kind != "found":
            raise ValueError("no answer")
        records = [bytes(entry).decode("utf-8", "replace") for entry in json.loads(rest)["data"][9]]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise Failed(f"the device's _adisk._tcp service could not be read ({exc}): {tail(out, 200)}") from exc
    names = []
    for record in records:
        key, _, value = record.partition("=")
        if re.fullmatch(r"dk\d+", key):
            fields = dict(part.partition("=")[::2] for part in value.split(","))
            if fields.get("adVN"):
                names.append(fields["adVN"])
    return names


def tail(data: bytes | str, size: int = 400) -> str:
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    return " ".join(text.split())[-size:]


class SmbResult:
    """What a run of commands in an smbclient session did: ok when smbclient ran all of them
    without an NT_STATUS error; ended when smbclient was no longer running."""

    def __init__(self, ok: bool, output: str, ended: bool) -> None:
        self.ok = ok
        self.output = output
        self.ended = ended

    @property
    def status(self) -> str:
        found = re.findall(r"NT_STATUS_[A-Z_]+", self.output)
        return found[-1] if found else ("smbclient ended" if self.ended else "no error")


class SmbListing:
    """smbclient -L: its exit status and output; disks are the shares it lists."""

    def __init__(self, rc: int, output: str) -> None:
        self.rc = rc
        self.output = output

    @property
    def ok(self) -> bool:
        return self.rc == 0

    @property
    def disks(self) -> list[str]:
        return [line.split("|")[1] for line in self.output.splitlines() if line.startswith("Disk|")]

    @property
    def status(self) -> str:
        found = re.findall(r"NT_STATUS_[A-Z_]+", self.output)
        return found[-1] if found else f"status {self.rc}"


class SmbPasswordChange(SmbListing):
    """smbpasswd -r: its exit status and output. It names a refusal of the server by Samba's
    description of the NT status (FRIENDLY_NT_STATUS; in Samba 4.22 "rejected to change the password
    with error: When trying to update a password, this status indicates that some password update
    rule has been violated. ..."), or by the status's name for one without a description, and an
    error of its own by the status. Status N, smbpasswd's exit status, stands for a text that is
    neither."""

    @property
    def status(self) -> str:
        found = re.findall(r"NT_STATUS_[A-Z_]+", self.output)
        if found:
            return found[-1]
        output = self.output.casefold()
        for text, status in FRIENDLY_NT_STATUS:
            if text.casefold() in output:
                return status
        return f"status {self.rc}"


# --- the device ------------------------------------------------------------------------------


class Device:
    """The dev device's host, through ssh.sh: every method runs one script of REMOTE_* there."""

    def __init__(self, ssh: Path = SSH, sd: Path = SD, timeout: int = 300, settings: dict[str, str] | None = None) -> None:
        self.ssh = ssh
        self.sd = sd
        self.timeout = timeout
        self.settings = settings or {}

    @staticmethod
    def command(args: tuple, read_only: bool = True) -> str:
        line = "bash -s --" + "".join(" " + shlex.quote(str(a)) for a in args)
        return "unshare --mount --propagation private " + line if read_only else line

    @staticmethod
    def payload(script: str, read_only: bool = True) -> str:
        return "{\n" + (RO_ROOT + "\n" if read_only else "") + script + "\n}\n"

    def run(self, script: str, *args: str, data: str = "", read_only: bool = True) -> str:
        """The script's standard output; standard input carries the script, then DATA."""
        result = subprocess.run(
            [str(self.ssh), self.command(args, read_only)],
            input=(self.payload(script, read_only) + data).encode(),
            capture_output=True,
            timeout=self.timeout,
        )
        if result.returncode != 0:
            raise Failed(f"the device answered status {result.returncode}: {tail(result.stderr)}")
        return result.stdout.decode("utf-8", "replace")

    def sectors(self) -> int | None:
        """The sectors written to the system disk since boot (sd.sh sectors), or None."""
        try:
            result = subprocess.run([str(self.sd), "sectors"], capture_output=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            return None
        text = result.stdout.decode().strip()
        return int(text) if result.returncode == 0 and text.isdigit() else None

    def stat(self, paths: list[str]) -> dict[str, str]:
        found = {}
        for line in self.run(REMOTE_STAT, *paths).splitlines():
            path, _, facts = line.partition("\t")
            found[path] = facts
        return found

    def read(self, path: str) -> str | None:
        out = self.run(REMOTE_READ, path).strip()
        return unb64(out[5:]) if out.startswith("file") else None

    def facts(self) -> dict[str, Any]:
        facts: dict[str, Any] = {"now": 0, "tools": [], "sha256": {}, "accounts": [], "group": "", "testparm": False,
                                 "passdb": "", "sizes": {}}
        for line in self.run(REMOTE_FACTS).splitlines():
            key, _, value = line.partition(" ")
            if key == "now" and value.isdigit():
                facts["now"] = int(value)
            elif key == "tool":
                facts["tools"].append(value)
            elif key == "sha256":
                path, _, digest = value.partition(" ")
                facts["sha256"][path] = digest
            elif key == "account":
                facts["accounts"].append(value)
            elif key == "group":
                facts["group"] = value
            elif key == "testparm":
                facts["testparm"] = value == "ok"
            elif key == "passdb":
                facts["passdb"] = value
            elif key == "size":
                path, _, size = value.partition(" ")
                facts["sizes"][path] = size
        return facts

    def passdb_entries(self) -> tuple[list[str], dict[str, str]]:
        """Samba's account names (pdbedit -L), and the values of its account policies of
        SAMBA_POLICIES by their keys: the value, or what pdbedit said instead."""
        names, policies = [], {key: "" for key in SAMBA_POLICIES}
        for line in self.run(REMOTE_SAMBA_USERS, read_only=False).splitlines():
            key, _, value = line.partition(" ")
            if key == "user":
                names.append(value)
            elif key in policies:
                found = re.search(r"(\d+)\s*$", value)
                policies[key] = found.group(1) if found else value
        return sorted(names), policies

    def subvolume(self, action: str, path: str) -> str:
        return self.run(REMOTE_SUBVOLUME, action, path).strip()

    def now(self) -> int:
        out = self.run(REMOTE_NOW).strip()
        if not out.isdigit():
            raise Failed(f"the device's time is {out!r}")
        return int(out)

    def quota_limit(self, path: str) -> str:
        """The limit of the volume's quota group in bytes, or "none"."""
        return self.run(REMOTE_QUOTA, path).strip()

    def adisk_shares(self) -> list[str] | None:
        """The shares that the device's _adisk._tcp service names, as Avahi resolves it (REMOTE_ADISK,
        adisk_names); None when Avahi resolves no such service."""
        return adisk_names(self.run(REMOTE_ADISK))

    def system_disk(self) -> str:
        return self.settings.get("UNOS_DEV_SYSTEM_DISK") or self.run(REMOTE_SYSTEM_DISK).strip()

    def scan(self, needles: list[str], since: int) -> dict[str, int]:
        names = {"supervisor": "the supervisor's log", "journal": "the journal", "samba": "Samba's log",
                 "audit": "the audit log"}
        found = {}
        for line in self.run(REMOTE_SCAN, str(since), data="".join(n + "\n" for n in needles)).splitlines():
            key, _, count = line.partition(" ")
            if key in names:
                found[names[key]] = int(count) if count.strip().isdigit() else -1
        return found

    def _smb_helper(self, mode: str, user: str, *lines: str) -> tuple[int, str]:
        """REMOTE_SMB's list, anonymous or change mode: its exit status and output. LINES go on
        standard input after the script."""
        out = self.run(REMOTE_SMB, mode, user, "", data="".join(line + "\n" for line in lines)).strip().split(" ")
        if len(out) < 2 or out[0] != "end":
            raise Failed("the SMB helper on the device gave no answer")
        return (int(out[1]) if out[1].isdigit() else -1), unb64(out[2] if len(out) > 2 else "")

    def smb_list(self, user: str, password: str) -> SmbListing:
        return SmbListing(*self._smb_helper("list", user, password))

    def smb_anonymous(self) -> SmbListing:
        return SmbListing(*self._smb_helper("anonymous", "", ""))

    def smb_change_password(self, user: str, old: str, new: str) -> SmbPasswordChange:
        """A change of USER's password from OLD to NEW over SAMR (smbpasswd -r 127.0.0.1), as
        Windows' Change a password asks for one (ADR-0342)."""
        return SmbPasswordChange(*self._smb_helper("change", user, old, new))

    def smb_session(self, user: str, password: str, share: str, protection: str = "") -> SmbSession:
        """PROTECTION: "" (signing required), smb2 (SMB 2.1 at most: no encryption) or encrypt
        (SMB3 encryption required)."""
        return SmbSession(self, user, password, share, protection=protection)

    def smb_guest_session(self, share: str) -> SmbSession:
        """A session to SHARE without a user and a password, as a null session signs in."""
        return SmbSession(self, "", "", share, mode="guest")

    def samba_files(self) -> dict[str, dict[str, str]]:
        """The files of /etc/samba: name -> sha256, mtime and mode."""
        found: dict[str, dict[str, str]] = {}
        for line in self.run(REMOTE_SAMBA_FILES).splitlines():
            parts = line.split(" ")
            if len(parts) == 6 and parts[0] == "file":
                found[parts[1]] = {"sha256": parts[2], "mtime": parts[3], "mode": f"{parts[4]} {parts[5]}"}
        return found

    def hand_edit(self, action: str, section: str, *digests: str) -> tuple[str, dict[str, str]]:
        """REMOTE_HAND_EDIT, without the read-only namespace: it writes /etc/samba (ADR-0362).
        DIGESTS: smb.conf's as the check read it (edit, again), or those of the check's edited
        files (restore). smb.conf's digest, and the copies smb.conf.unos-replaced-<time>: name ->
        digest."""
        out = self.run(REMOTE_HAND_EDIT, action, section, *digests, read_only=False)
        conf = [line[len("conf "):] for line in out.splitlines() if line.startswith("conf ")]
        copies = dict(line[len("copy "):].split(" ", 1) for line in out.splitlines()
                      if line.startswith("copy ") and line.count(" ") == 2)
        if len(conf) != 1:
            raise Failed(f"the edit of smb.conf answered {tail(out)!r}")
        return conf[0], copies

    def unserve(self, action: str, path: str) -> str:
        return self.run(REMOTE_UNSERVE, action, path).strip()

    def entries(self, action: str, path: str, acl: bool = False) -> dict[str, str]:
        """REMOTE_ENTRIES repair or sticky: the other account, {"user", "group", "uid"}."""
        out = self.run(REMOTE_ENTRIES, action, path, "acl" if acl else "noacl")
        for line in out.splitlines():
            parts = line.split(" ")
            if len(parts) == 3 and parts[0] == "account" and ":" in parts[1] and parts[2].isdigit():
                user, _, group = parts[1].partition(":")
                return {"user": user, "group": group, "uid": parts[2]}
        raise Failed(f"the helper on the device answered {tail(out)!r}")

    def acl(self, path: str) -> list[str]:
        """The entries of PATH's ACL as getfacl prints them ("user:nobody:r--")."""
        return [line[4:] for line in self.run(REMOTE_ENTRIES, "acl", path).splitlines() if line.startswith("acl ")]

    def pool_folders(self, action: str = "list", name: str = "") -> list[str]:
        """REMOTE_POOL_FOLDER: make and remove run without the read-only namespace, since the pools'
        base is a folder of the root filesystem. The folders of the base afterwards."""
        return sorted(self.run(REMOTE_POOL_FOLDER, action, name, read_only=action == "list").split())

    def restart_supervisor(self) -> int:
        """Restart the supervisor's container; the seconds until it was healthy again."""
        out = self.run(REMOTE_RESTART).split()
        if len(out) != 2 or out[0] != "healthy" or not out[1].isdigit():
            raise Failed(f"the restart answered {' '.join(out)!r}")
        return int(out[1])

    def archive_members(self, name: str, passphrase: str = "") -> list[str]:
        """The members of the backup NAME (REMOTE_ARCHIVE); an encrypted one is opened with
        PASSPHRASE, which goes on standard input."""
        out = self.run(REMOTE_ARCHIVE, name, data=passphrase + "\n")
        return [line[len("member "):] for line in out.splitlines() if line.startswith("member ")]

    def db_copies(self, action: str = "list", names: tuple[str, ...] = ()) -> list[str]:
        """REMOTE_DB_COPIES: the copies of the database from before a restore, after removing NAMES
        (remove runs without the read-only namespace: it writes the data volume)."""
        out = self.run(REMOTE_DB_COPIES, action, *names, read_only=action == "list")
        return sorted(line[len("copy "):] for line in out.splitlines() if line.startswith("copy "))


class SmbSession:
    """One smbclient session on the device (REMOTE_SMB's session mode), held open while the
    check calls the API: run() sends commands and waits for their output."""

    def __init__(self, device: Device, user: str, password: str, share: str, timeout: int = 90,
                 mode: str = "session", protection: str = "") -> None:
        self.timeout = timeout
        self.n = 0
        self.ended = False
        self.buffer = b""
        self.errors = tempfile.TemporaryFile()  # the helper's standard error, for a failure's message
        self.proc = subprocess.Popen(
            [str(device.ssh), device.command((mode, user, share, protection))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors,
        )
        self._send(device.payload(REMOTE_SMB) + password + "\n")

    def __enter__(self) -> SmbSession:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _send(self, text: str) -> bool:
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(text.encode())
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError):
            return False

    def _line(self) -> str:
        """The next line from the device, within the timeout."""
        assert self.proc.stdout is not None
        deadline = time.monotonic() + self.timeout
        with selectors.DefaultSelector() as selector:
            selector.register(self.proc.stdout, selectors.EVENT_READ)
            while b"\n" not in self.buffer:
                left = deadline - time.monotonic()
                if left <= 0 or not selector.select(left):
                    raise Failed(f"the SMB helper on the device did not answer within {self.timeout} s{self.stderr()}")
                chunk = os.read(self.proc.stdout.fileno(), 65536)
                if not chunk:
                    raise Failed(f"the SMB helper on the device ended{self.stderr()}")
                self.buffer += chunk
        line, _, self.buffer = self.buffer.partition(b"\n")
        return line.decode("utf-8", "replace")

    def stderr(self) -> str:
        self.errors.seek(0)
        text = tail(self.errors.read(), 300)
        return f": {text}" if text else ""

    def run(self, *commands: str) -> SmbResult:
        if self.ended:
            return SmbResult(False, "smbclient had ended", True)
        self.n += 1
        if not self._send("".join(c + "\n" for c in commands) + f"pause {self.n}\n"):
            self.ended = True
            return SmbResult(False, "the SMB helper on the device ended", True)
        kind, _, rest = self._line().partition(" ")
        _, _, encoded = rest.partition(" ")
        output = unb64(encoded.strip())
        output = re.sub(r"smb: .*?> ", "", output.replace(f"@@unos-scenario-{self.n}@@", "")).strip()
        if kind != "at":
            self.ended = True
            return SmbResult(False, output, True)
        return SmbResult("NT_STATUS_" not in output, output, False)

    def close(self) -> None:
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        if self.proc.stdout is not None:
            self.proc.stdout.close()
        self.errors.close()


# --- a check ---------------------------------------------------------------------------------


class Step:
    def __init__(self, text: str) -> None:
        self.text = text


class InSteps:
    """The device for a check: its methods may run only inside a step or the clean-up, where a
    failure becomes a FAIL line (sectors, the SD measurement, anywhere)."""

    def __init__(self, device: Any, check: Check) -> None:
        self._device = device
        self._check = check

    def __getattr__(self, name: str) -> Any:
        found = getattr(self._device, name)
        if callable(found) and name != "sectors" and not self._check.inside:
            raise AssertionError(f"the check calls device.{name} outside a step")
        return found


class Check:
    """One area's check: its steps, its clean-ups and the comparison with its baseline.

    api: api.DeviceApi (or what has its request(), upload(), as_user() and secrets); device:
    Device; interrupts: the exceptions that count as an interruption."""

    def __init__(self, area: str, api: Any, device: Any, pool: str | None = None, out: Any = None,
                 run_id: str | None = None, interrupts: tuple = (KeyboardInterrupt,), signals: bool = False) -> None:
        self.area = area
        self.api = api
        self.device = InSteps(device, self)
        self.inside = 0
        self.pool_name = pool
        self.out = out or sys.stdout
        self.run = run_id or MARK + secrets.token_hex(3)
        self.interrupts = interrupts
        self.signals = signals
        self.passed = self.failed = self.skipped = 0
        self.number = 0
        self.cleanups: list[tuple[str, Callable[[], None]]] = []
        self.temporary: list[str] = []
        self.answers: list[str] = []
        self.read_state: Callable[[], dict] | None = None
        self.before: dict | None = None
        self.last_sectors: int | None = None
        self.output_lost = False

    # output

    def redact(self, text: str) -> str:
        """TEXT without a secret, also as JSON writes one (a password with a tab) and as tail()
        joins its whitespace: the longest first, so that no part of a longer one is left."""
        found = {form for p in [*getattr(self.api, "secrets", []), *self.temporary] for form in secret_forms(p)}
        return relay.redact_text(text, sorted(found, key=len, reverse=True))

    def say(self, text: str) -> None:
        """Print a line. A closed terminal or pipe (after SIGHUP, say) loses the line, never the clean-up."""
        if self.output_lost:
            return
        try:
            self.out.write(self.redact(text) + "\n")
            self.out.flush()
        except (OSError, ValueError):
            self.output_lost = True

    def sd(self) -> str:
        now = self.device.sectors()
        last, self.last_sectors = self.last_sectors, now
        if now is None or last is None:
            return "SD unknown"
        return f"SD {(now - last) * 512 / 1048576:.3f} MiB"

    def secret(self, value: str | None = None) -> str:
        """A new temporary password, or VALUE, one made of random text here: redacted wherever it
        would be printed, and looked for in the answers and the logs."""
        value = secrets.token_urlsafe(18) if value is None else value
        self.temporary.append(value)
        return value

    # steps

    @contextlib.contextmanager
    def step(self, text: str) -> Iterator[Step]:
        self.number += 1
        step = Step(text)
        self.inside += 1
        try:
            yield step
        except Failed as e:
            self.failed += 1
            self.say(f"FAIL  {self.number}. {step.text}: {e}  [{self.sd()}]")
            raise Stop() from None
        except relay.ApiError as e:
            self.failed += 1
            self.say(f"FAIL  {self.number}. {step.text}: the API could not be asked: {e}  [{self.sd()}]")
            raise Stop() from None
        except subprocess.TimeoutExpired as e:
            self.failed += 1
            self.say(f"FAIL  {self.number}. {step.text}: no answer from the device within {e.timeout} s  [{self.sd()}]")
            raise Stop() from None
        except OSError as e:
            self.failed += 1
            self.say(f"FAIL  {self.number}. {step.text}: the device could not be reached: {e}  [{self.sd()}]")
            raise Stop() from None
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as e:
            self.failed += 1
            self.say(f"FAIL  {self.number}. {step.text}: an answer of an unexpected shape ({type(e).__name__}: {e})"
                     f"  [{self.sd()}]")
            raise Stop() from None
        finally:
            self.inside -= 1
        self.passed += 1
        self.say(f"PASS  {self.number}. {step.text}  [{self.sd()}]")
        if self.output_lost:  # nobody reads the results any more: clean up instead
            self.failed += 1
            raise Stop()

    def skip(self, text: str, why: str) -> None:
        self.number += 1
        self.skipped += 1
        self.say(f"SKIP  {self.number}. {text}: {why}  [{self.sd()}]")

    @staticmethod
    def expect(condition: object, why: str) -> None:
        if not condition:
            raise Failed(why)

    def on_cleanup(self, text: str, func: Callable[[], None]) -> None:
        self.cleanups.append((text, func))

    # the API

    def call(self, method: str, path: str, body: Any = None, params: dict | None = None, expect: Any = 200,
             api: Any = None) -> Any:
        """API: another user's (self.api.as_user), else the administrator's."""
        expected = (expect,) if isinstance(expect, int) else tuple(expect)
        if not self.inside:
            raise AssertionError(f"the check calls {method} {path} outside a step")
        answer = (api or self.api).request(method, path, json_body=body, params=params)
        self.answers.append(answer.text)
        if answer.status_code not in expected:
            raise Failed(f"{method} {path} answered HTTP {answer.status_code}{self.detail(answer)}"
                         f" (expected {' or '.join(map(str, expected))})")
        try:
            return answer.json()
        except ValueError:
            return answer.text

    @staticmethod
    def detail(answer: Any) -> str:
        try:
            detail = answer.json().get("detail")
        except (ValueError, AttributeError):
            detail = answer.text
        if not detail:
            return ""
        return ": " + tail(detail if isinstance(detail, str) else json.dumps(detail), 300)

    def get(self, path: str, params: dict | None = None, expect: Any = 200, api: Any = None) -> Any:
        return self.call("GET", path, params=params, expect=expect, api=api)

    def post(self, path: str, body: Any = None, params: dict | None = None, expect: Any = 200, api: Any = None) -> Any:
        return self.call("POST", path, body, params, expect, api)

    def upload(self, folder: str, name: str, text: str) -> None:
        if not self.inside:
            raise AssertionError("the check uploads outside a step")
        answer = self.api.upload("/files/upload", {"path": folder}, name, text.encode())
        self.answers.append(answer.text)
        if answer.status_code != 200:
            raise Failed(f"POST /files/upload answered HTTP {answer.status_code}{self.detail(answer)} (expected 200)")

    def download(self, path: str) -> str:
        return self.call("GET", "/files/download", params={"path": path})

    def delete_for_good(self, path: str) -> None:
        self.call("DELETE", "/files/delete", [path], {"permanent": "true"}, expect=(200, 404))

    def delete_share(self, name: str) -> None:
        self.call("DELETE", f"/shares/smb/{name}", expect=(200, 404))

    def delete_user(self, uid: int, name: str) -> None:
        self.call("DELETE", f"/auth/users/{uid}", params={"username": name}, expect=(200, 404))

    def delete_user_named(self, name: str) -> None:
        """The user NAME, found in the list, so that a create whose answer was lost is removed too."""
        for user in self.get("/auth/users"):
            if user["username"] == name:
                self.delete_user(user["id"], name)

    def scanned(self, since: int, sources: tuple[str, ...]) -> None:
        """No temporary secret in an answer of the API, nor in the device's logs SOURCES since SINCE,
        also as JSON writes it (a password with a tab)."""
        forms = sorted({form for p in self.temporary for form in secret_forms(p)})
        self.expect(not [a for a in self.answers for p in forms if p in a], "an answer of the API holds one")
        found = self.device.scan(forms, since)
        unread = [k for k in sources if found.get(k, -1) < 0]
        self.expect(not unread, f"{', '.join(unread)} could not be read")
        hits = {k: found[k] for k in sources if found[k]}
        self.expect(not hits, f"lines with one: {hits}")

    def delete_volume(self, pool: str, name: str) -> None:
        self.call("DELETE", f"/storage/pools/{pool}/volumes/{name}", expect=(200, 404))

    # the baseline and the end

    def pick_pool(self) -> dict:
        """The pool of --pool, else the one pool that is mounted read-write and managed."""
        pools = self.get("/storage/pools")
        usable = [p for p in pools if p.get("mounted") and p.get("persistent") and not p.get("read_only")
                  and p.get("mountpoint")]
        if self.pool_name:
            found = [p for p in pools if p.get("name") == self.pool_name]
            self.expect(found, f"there is no pool {self.pool_name}")
            self.expect(found[0] in usable, f"the pool {self.pool_name} is not mounted read-write with UNOS's fstab entry")
            return found[0]
        names = ", ".join(p["name"] for p in usable) or "none"
        self.expect(len(usable) == 1, f"{len(usable)} pools are mounted read-write with UNOS's fstab entry ({names}): "
                                      "name one with --pool")
        return usable[0]

    def baseline(self, read: Callable[[], dict]) -> None:
        """Read how the device is; the end compares with it. A name of an earlier check fails."""
        self.read_state = read
        self.before = read()
        left = sorted({m.group(0) for value in self.before.values() for m in LEFTOVER.finditer(json.dumps(value))})
        self.expect(not left, "it holds leftovers of an earlier check, " + ", ".join(left) + ", in "
                    + ", ".join(k for k, v in self.before.items() if LEFTOVER.search(json.dumps(v)))
                    + ": remove them (scripts/dev/api.py), then run the check again")

    def clean_up(self) -> None:
        while self.cleanups:
            text, func = self.cleanups.pop()
            self.inside += 1
            try:
                func()
            except (Failed, relay.ApiError, subprocess.TimeoutExpired, OSError) as e:
                self.failed += 1
                self.say(f"FAIL  clean up: {text}: {e}  [{self.sd()}]")
            except (KeyError, IndexError, TypeError, ValueError, AttributeError) as e:
                self.failed += 1
                self.say(f"FAIL  clean up: {text}: an answer of an unexpected shape ({type(e).__name__}: {e})"
                         f"  [{self.sd()}]")
            else:
                self.say(f"done  clean up: {text}  [{self.sd()}]")
            finally:
                self.inside -= 1

    def compare(self) -> None:
        if self.read_state is None or self.before is None:
            return
        try:
            with self.step("the device is as the check found it"):
                now = self.read_state()
                changed = [key for key in self.before if now.get(key) != self.before[key]]
                self.expect(not changed, "; ".join(f"{key}: {describe(self.before[key], now.get(key))}" for key in changed))
        except Stop:
            pass

    @contextlib.contextmanager
    def held_signals(self) -> Iterator[None]:
        """SIGINT, SIGTERM and SIGHUP wait while the clean-up runs (only for main()): they are
        blocked, so they stay pending, and the commands that the clean-up starts inherit the
        block, so a Ctrl-C does not end them either; once unblocked, a pending one interrupts."""
        if not self.signals:
            yield
            return
        names = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}
        old = signal.pthread_sigmask(signal.SIG_BLOCK, names)
        try:
            yield
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, old)

    def execute(self, check: Callable[[Check], None]) -> int:
        self.last_sectors = self.device.sectors()
        self.say(f"== scenario {self.area} (run {self.run})")
        interrupted = False
        try:
            check(self)
        except Stop:
            pass
        except self.interrupts:
            interrupted = True
            self.failed += 1
            self.say("interrupted: removing what the check made")
        finally:
            try:
                with self.held_signals():
                    self.clean_up()
                    self.compare()
            except self.interrupts:  # a signal that waited for the clean-up
                if not interrupted:
                    interrupted = True
                    self.failed += 1
                    self.say("interrupted during the clean-up, which ran to its end")
        if self.failed:
            self.say(f"scenario {self.area}: FAILED, {self.passed} steps passed, {self.failed} failed")
        else:
            self.say(f"scenario {self.area}: OK, {self.passed} steps passed"
                     + (f", {self.skipped} skipped" if self.skipped else ""))
        return 130 if interrupted else (1 if self.failed else 0)


def describe(before: Any, after: Any) -> str:
    if isinstance(before, dict) and isinstance(after, dict):  # file -> digest: only which files changed
        return "changed: " + ", ".join(sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k)))
    if isinstance(before, list) and isinstance(after, list):
        added = [x for x in after if x not in before]
        gone = [x for x in before if x not in after]
        parts = ([f"new {', '.join(map(str, added))}"] if added else []) + ([f"gone {', '.join(map(str, gone))}"] if gone else [])
        return "; ".join(parts) or "in another order"
    if isinstance(before, str) and len(before) == 64 and isinstance(after, str):
        return "its content changed"
    return f"{before} before, {after} now"


def stat_is(c: Check, facts: dict[str, str], path: str, want: str) -> None:
    c.expect(facts.get(path) == want, f"{path} is {facts.get(path)}, not {want}")


# --- files -----------------------------------------------------------------------------------


def files_check(c: Check) -> None:
    with c.step("a pool is mounted read-write and managed by UNOS") as s:
        pool = c.pick_pool()
        name, root = pool["name"], pool["mountpoint"]
        s.text += f": {name} at {root}"
    folder = f"{root}/{c.run}"
    volume = c.run + "v"
    vpath = f"{root}/{volume}"

    def state() -> dict:
        return {
            "the pool's top folder": sorted(i["name"] for i in c.get("/files/list", params={"path": root})["items"]),
            "the trash": sorted(f"{i['path']} from {i.get('original_path')}" for i in c.get("/files/trash")["items"]),
            "the volumes": sorted(v["name"] for v in c.get(f"/storage/pools/{name}/volumes")),
        }

    def own_trash() -> list[dict]:
        return [i for i in c.get("/files/trash")["items"] if c.run in (i.get("original_path") or "")]

    def empty_own_trash() -> None:
        items = [i["path"] for i in own_trash()]
        if items:
            c.call("DELETE", "/files/trash", items)

    def listed(path: str) -> list[str]:
        return [i["name"] for i in c.get("/files/list", params={"path": path})["items"]]

    with c.step("the device holds nothing of an earlier check"):
        c.baseline(state)

    with c.step(f"New Folder makes {folder} with 755 root:root, outside share folders"):
        c.on_cleanup(f"delete {folder} for good", lambda: c.delete_for_good(folder))
        c.post("/files/mkdir", {"path": root, "name": c.run})
        stat_is(c, c.device.stat([folder]), folder, "755 root:root")

    with c.step("Upload puts a.txt there with 644 root:root, and Download returns its bytes"):
        c.upload(folder, "a.txt", DATA)
        stat_is(c, c.device.stat([f"{folder}/a.txt"]), f"{folder}/a.txt", "644 root:root")
        c.expect(c.download(f"{folder}/a.txt") == DATA, "Download returned other bytes")

    with c.step("Rename turns a.txt into b.txt"):
        c.post("/files/rename", {"path": f"{folder}/a.txt", "new_name": "b.txt"})
        names = listed(folder)
        c.expect("b.txt" in names and "a.txt" not in names, f"the folder lists {names}")

    with c.step("Copy puts b.txt into a new folder, and Move takes the copy on into another"):
        for sub in ("copied", "moved"):
            c.post("/files/mkdir", {"path": folder, "name": sub})
        c.post("/files/copy", [f"{folder}/b.txt"], {"destination": f"{folder}/copied"})
        c.expect(c.download(f"{folder}/copied/b.txt") == DATA, "the copy holds other bytes")
        c.post("/files/move", [f"{folder}/copied/b.txt"], {"destination": f"{folder}/moved"})
        c.expect(listed(f"{folder}/copied") == [] and listed(f"{folder}/moved") == ["b.txt"],
                 "the copy is not in moved/ alone")

    trash = f"{root}/.unos-trash"
    with c.step(f"Delete moves b.txt into the pool's trash {trash} (700 root:root), and the folder no longer lists it"):
        c.on_cleanup("delete the check's items in the trash for good", empty_own_trash)
        c.call("DELETE", "/files/delete", [f"{folder}/b.txt"])
        c.expect("b.txt" not in listed(folder), "the folder still lists b.txt")
        items = own_trash()
        c.expect(len(items) == 1 and items[0]["original_path"] == f"{folder}/b.txt"
                 and items[0]["path"].startswith(trash + "/"), f"the trash lists {items}")
        stat_is(c, c.device.stat([trash]), trash, "700 root:root")

    with c.step("Restore puts b.txt back with its bytes"):
        c.post("/files/trash/restore", [items[0]["path"]])
        c.expect(c.download(f"{folder}/b.txt") == DATA and not own_trash(), "b.txt is not back, or still in the trash")

    with c.step("a restore is refused while the name is taken again, and the item stays in the trash"):
        c.call("DELETE", "/files/delete", [f"{folder}/b.txt"])
        c.upload(folder, "b.txt", DATA2)
        items = own_trash()
        c.post("/files/trash/restore", [i["path"] for i in items], expect=400)
        c.expect(len(own_trash()) == 1 and c.download(f"{folder}/b.txt") == DATA2, "the restore changed something")

    with c.step("Delete for good in the trash removes the item"):
        c.call("DELETE", "/files/trash", [items[0]["path"]])
        c.expect(not own_trash(), "the trash still lists it")

    with c.step(f"a volume {volume} for the next steps, with a file in it"):
        c.on_cleanup(f"delete the volume {volume}", lambda: c.delete_volume(name, volume))
        c.post(f"/storage/pools/{name}/volumes", {"name": volume, "quota": None})
        c.upload(vpath, "v.txt", DATA)

    with c.step("Delete, Rename and Move of the volume are refused with 409 and change nothing"):
        c.call("DELETE", "/files/delete", [vpath], expect=409)
        c.post("/files/rename", {"path": vpath, "new_name": volume + "x"}, expect=409)
        c.post("/files/move", [vpath], {"destination": folder}, expect=409)
        volumes = [v["name"] for v in c.get(f"/storage/pools/{name}/volumes")]
        c.expect(volume in volumes and listed(vpath) == ["v.txt"], "the volume changed")

    with c.step("Copy of the volume makes a folder with its files"):
        c.post("/files/copy", [vpath], {"destination": folder})
        c.expect(c.download(f"{folder}/{volume}/v.txt") == DATA, "the copy lacks v.txt")

    vtrash = f"{vpath}/.unos-trash"
    with c.step("a file deleted in the volume goes into the volume's own trash, and comes back"):
        c.call("DELETE", "/files/delete", [f"{vpath}/v.txt"])
        items = own_trash()
        c.expect(len(items) == 1 and items[0]["path"].startswith(vtrash + "/"), f"the trash lists {items}")
        stat_is(c, c.device.stat([vtrash]), vtrash, "700 root:root")
        c.post("/files/trash/restore", [items[0]["path"]])
        c.expect(c.download(f"{vpath}/v.txt") == DATA, "v.txt is not back")

    nest = f"{folder}/nest"
    with c.step("Delete of a folder with a subvolume below it is refused with 409 and deletes nothing"):
        c.post("/files/mkdir", {"path": folder, "name": "nest"})
        c.on_cleanup(f"delete the subvolume {nest}/sub", lambda: c.expect(
            c.device.subvolume("delete", f"{nest}/sub") == "none", "it is still there"))
        c.expect(c.device.subvolume("create", f"{nest}/sub") == "subvolume", "btrfs made no subvolume")
        c.upload(nest, "n.txt", DATA)
        c.call("DELETE", "/files/delete", [nest], expect=409)
        facts = c.device.stat([f"{nest}/n.txt", f"{nest}/sub"])
        c.expect("missing" not in facts.values(), f"something went: {facts}")


# --- volumes ---------------------------------------------------------------------------------


def volumes_check(c: Check) -> None:
    with c.step("a pool is mounted read-write and managed by UNOS") as s:
        pool = c.pick_pool()
        name, root = pool["name"], pool["mountpoint"]
        s.text += f": {name} at {root}"
    volume, quoted, share, snap = c.run, c.run + "q", c.run + "s", c.run
    vpath = f"{root}/{volume}"

    def volumes() -> list[dict]:
        return c.get(f"/storage/pools/{name}/volumes")

    def copies() -> list[str]:
        return [x["path"] for x in c.get("/snapshots/copies", {"subvolume": vpath})]

    def snapshots() -> list[str]:
        return [x["path"] for x in c.get("/snapshots/", {"subvolume": vpath})]

    made_copies: list[str] = []  # the names of the restored copies whose restore answered

    def remove_volume() -> None:
        """The check's volume, with the restored copies and snapshots in it, which its Delete refuses
        (all of them the check's): those that their lists name, so that a call whose answer was lost
        is undone too, and the copies that the check made, also where the list misses one. A 404 of
        the copies' list: the volume is gone."""
        listed = c.get("/snapshots/copies", {"subvolume": vpath}, expect=(200, 404))
        if isinstance(listed, list):
            for copy in sorted({x["name"] for x in listed} | set(made_copies)):
                c.call("DELETE", f"/snapshots/copies/{copy}", params={"subvolume": vpath}, expect=(200, 404))
            for snapshot in c.get("/snapshots/", {"subvolume": vpath}):
                c.call("DELETE", f"/snapshots/{snapshot['name']}", params={"subvolume": vpath}, expect=(200, 404))
        c.delete_volume(name, volume)

    def state() -> dict:
        return {"the volumes": sorted(v["name"] for v in volumes()),
                "the shares": sorted(s["name"] for s in c.get("/shares/smb"))}

    with c.step("the device holds nothing of an earlier check"):
        c.baseline(state)

    with c.step(f"a volume without a quota is made at {vpath}, listed and a subvolume on the host"):
        c.on_cleanup(f"delete the volume {volume} with its restored copies and snapshots", remove_volume)
        made = c.post(f"/storage/pools/{name}/volumes", {"name": volume, "quota": None})
        c.expect(made.get("path") == vpath, f"it is at {made.get('path')}")
        c.expect(volume in [v["name"] for v in volumes()], "it is not listed")
        c.expect(c.device.subvolume("show", vpath) == "subvolume", "btrfs does not show it as a subvolume")

    with c.step("a volume with a quota of 1 GiB") as s:
        c.on_cleanup(f"delete the volume {quoted}", lambda: c.delete_volume(name, quoted))
        answer = c.post(f"/storage/pools/{name}/volumes", {"name": quoted, "quota": GIB}, expect=(200, 409))
        if isinstance(answer, dict) and "path" in answer:
            limit = c.device.quota_limit(answer["path"])
            c.expect(limit == str(GIB), f"btrfs shows the limit {limit} on {answer['path']}, not {GIB}")
            c.delete_volume(name, quoted)
            c.expect(quoted not in [v["name"] for v in volumes()], "it is still listed after its Delete")
            s.text += " is made with a limit of 1 GiB on its quota group, and deleted"
        else:
            c.expect("not enabled" in json.dumps(answer).lower(), f"the 409 is not the one for quotas that are off: {answer}")
            c.expect(quoted not in [v["name"] for v in volumes()], "it was made anyway")
            s.text += " is refused while quotas are off on the pool"

    with c.step(f"Delete is refused while the share {share} uses the volume, and names the share"):
        c.on_cleanup(f"delete the share {share}", lambda: c.delete_share(share))
        c.post("/shares/smb", {"name": share, "path": vpath})
        answer = c.call("DELETE", f"/storage/pools/{name}/volumes/{volume}", expect=409)
        c.expect(share in json.dumps(answer), f"the 409 does not name the share: {answer}")
        c.expect(volume in [v["name"] for v in volumes()], "the volume is gone")

    with c.step(f"a snapshot {snap} of the volume is made and listed"):
        made = c.post("/snapshots/", {"subvolume": vpath, "name": snap})
        c.expect(made.get("path") == f"{vpath}/.snapshots/{snap}", f"it is at {made.get('path')}")
        c.expect(made["path"] in snapshots(), "it is not listed")

    # storage.restore-copies-deletable (ADR-0358): the copy that a restore makes in the volume is
    # listed and deleted on the Snapshots page, and the volume can be deleted after it.
    with c.step("Restore of the snapshot makes a copy in the volume, a subvolume on the host, listed with the "
                "volume's restored copies and not with its snapshots") as s:
        target = c.post("/snapshots/restore", {"snapshot_path": made["path"]})["target"]
        s.text += f": {target}"
        copy = target.rsplit("/", 1)[1]
        made_copies.append(copy)
        c.expect(target == f"{vpath}/{copy}" and re.fullmatch(r"restore_\d{8}_\d{6}", copy),
                 "it is not restore_<date>_<time> right in the volume")
        c.expect(target in copies(), "it is not listed with the volume's restored copies")
        c.expect(target not in snapshots(), "it is listed with the volume's snapshots")
        c.expect(c.device.subvolume("show", target) == "subvolume", "btrfs does not show it as a subvolume")

    with c.step(f"the copy's Delete is refused while the share {share} uses it, and names the share"):
        c.delete_share(share)
        c.post("/shares/smb", {"name": share, "path": target})
        answer = c.call("DELETE", f"/snapshots/copies/{copy}", params={"subvolume": vpath}, expect=409)
        c.expect(share in json.dumps(answer), f"the 409 does not name the share: {answer}")
        c.expect(target in copies(), "the copy is gone")

    with c.step("with the share deleted, the copy's Delete removes it, also on the host"):
        c.delete_share(share)
        c.call("DELETE", f"/snapshots/copies/{copy}", params={"subvolume": vpath})
        c.expect(target not in copies(), "it is still listed")
        c.expect(c.device.subvolume("show", target) == "none", "btrfs still shows it")

    with c.step("with its snapshot deleted, Delete removes the volume, also on the host"):
        c.call("DELETE", f"/snapshots/{snap}", params={"subvolume": vpath})
        c.call("DELETE", f"/storage/pools/{name}/volumes/{volume}")
        c.expect(volume not in [v["name"] for v in volumes()], "it is still listed")
        stat_is(c, c.device.stat([vpath]), vpath, "missing")


# --- SMART -----------------------------------------------------------------------------------


def smart_check(c: Check) -> None:
    with c.step("Storage lists the whole disks, without the system disk") as s:
        disks = c.get("/storage/disks")
        system = c.device.system_disk()
        names = [d["name"] for d in disks]
        c.expect(names, "no disk is listed")
        c.expect(system and system not in names, f"the system disk {system} is listed")
        s.text += f" {system}: " + ", ".join(names)

    with c.step("Disk Health lists the same disks"):
        health = c.get("/health/disks")["disks"]
        c.expect(sorted(d["device"] for d in health) == sorted(d["path"] for d in disks),
                 f"Disk Health lists {[d['device'] for d in health]}")

    for disk in disks:
        with c.step(f"the SMART data of {disk['name']} open on Disk Health and on Storage") as s:
            info = c.get(f"/health/disk/{disk['name']}")
            data = c.get(f"/storage/disks/{disk['name']}/smart")
            c.expect(info.get("device") == disk["path"] and data.get("device") == disk["path"],
                     f"they are of {info.get('device')} and {data.get('device')}")
            c.expect(info.get("health_status") in ("PASSED", "FAILED", "UNKNOWN"), f"health {info.get('health_status')}")
            s.text += f": {disk['name']}: {info.get('health_status')}, {info.get('temperature')} C, " \
                      f"{len(info.get('attributes') or [])} attributes"

    with c.step("the system disk, a partition and a name that is no disk of this system are refused with 400") as s:
        absent = next(n for n in ("sdz", "sdy", "sdx", "sdw") if n not in names)
        partitions = [p["name"] for d in disks for p in (d.get("partitions") or []) if p.get("name")][:1]
        refused = [system, absent] + partitions
        for name in refused:
            c.get(f"/health/disk/{name}", expect=400)
            c.get(f"/storage/disks/{name}/smart", expect=400)
        s.text += ": " + ", ".join(refused) + ("" if partitions else " (no listed disk has a partition)")

    for disk in disks:
        with c.step(f"a short self-test starts on {disk['name']}"):
            answer = c.post(f"/health/disk/{disk['name']}/test", params={"test_type": "short"})
            c.expect(answer.get("status") == "started", f"the answer is {answer}")


# --- backups ---------------------------------------------------------------------------------


def backups_check(c: Check) -> None:
    made: list[str] = []  # the names the API gave the check's backups

    def names() -> list[str]:
        return sorted(b["filename"] for b in c.get("/backup/list")["backups"])

    def manual() -> list[str]:
        """The backups made by hand (unos_backup_...), not the Scheduler's (auto_backup_...)."""
        return [n for n in names() if n.startswith("unos_backup_")]

    def remove_made() -> None:
        """Delete the check's backups. One whose answer was lost has no name the check knows: it is
        named, never deleted, since a backup made by hand meanwhile would look the same."""
        for name in made:
            c.call("DELETE", f"/backup/{name}", expect=(200, 404))
        unknown = [n for n in manual() if n not in c.before["the backups made by hand"] and n not in made]
        c.expect(not unknown, "new backups whose names the check never got, " + ", ".join(unknown)
                 + ": delete each one that is the check's (scripts/dev/api.py DELETE /backup/<name>)")

    with c.step("the backups are listed"):
        c.baseline(lambda: {"the backups made by hand": manual()})
        since = c.device.now()
        c.on_cleanup("delete the check's backups", remove_made)

    with c.step("a plain backup is made and listed"):
        answer = c.post("/backup/create", {})
        plain = answer["backup"]["filename"]
        made.append(plain)
        c.expect(not answer.get("encrypted") and plain in names(), f"{plain} is not listed as a plain backup")

    passphrase, wrong = c.secret(), c.secret()
    with c.step("an encrypted backup is made with a passphrase and listed"):
        answer = c.post("/backup/create", {"encrypt": True, "passphrase": passphrase})
        sealed = answer["backup"]["filename"]
        made.append(sealed)
        listed = [b for b in c.get("/backup/list")["backups"] if b["filename"] == sealed]
        c.expect(sealed.endswith(".tar.gz.enc") and listed and listed[0].get("encrypted"), f"it is listed as {listed}")

    with c.step("a restore of a missing backup answers 404"):
        c.post(f"/backup/restore/{c.run}.tar.gz", expect=404)

    with c.step("the encrypted backup's restore is refused without its passphrase and with a wrong one"):
        users = sorted(u["username"] for u in c.get("/auth/users"))
        c.post(f"/backup/restore/{sealed}", {}, expect=400)
        c.post(f"/backup/restore/{sealed}", {"passphrase": wrong}, expect=400)
        c.expect(sorted(u["username"] for u in c.get("/auth/users")) == users, "the users changed")

    with c.step("no passphrase of the check is in an answer, the supervisor's log, the journal or the audit log"):
        c.scanned(since, ("the supervisor's log", "the journal", "the audit log"))

    with c.step("Delete removes both backups"):
        for name in (plain, sealed):
            c.call("DELETE", f"/backup/{name}")
        c.expect(plain not in names() and sealed not in names(), "a backup is still listed")


# --- SMB -------------------------------------------------------------------------------------


def smb_section(conf: str, name: str) -> dict[str, str] | None:
    """The lines of smb.conf's section NAME (in any case), or None when it has none."""
    found: dict[str, str] | None = None
    section = None
    for line in conf.splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line.strip("[]").lower()
            if section == name.lower() and found is None:
                found = {}
        elif found is not None and section == name.lower() and "=" in line and not line.startswith(("#", ";")):
            key, _, value = line.partition("=")
            found[" ".join(key.split()).lower()] = value.strip()
    return found


def smb_global(conf: str) -> dict[str, str]:
    """The [global] lines of smb.conf."""
    return smb_section(conf, "global") or {}


def marked_account(c: Check, user: str) -> None:
    """USER has a marked host account (UNOS's uid range, the group unos-smb, no login) and a
    Samba account."""
    lines = [line for line in c.device.facts()["accounts"] if line.split(":")[0] == user]
    c.expect(lines, "getent passwd has no such account")
    fields = lines[0].split(":")
    c.expect(int(fields[2]) in SMB_UIDS and fields[3] == str(SMB_GID) and fields[4] == "UNOS SMB user"
             and fields[6].endswith("nologin"), f"its account is {lines[0]}")
    c.expect(user in c.device.passdb_entries()[0], "pdbedit -L does not list it")


def passdb_time(c: Check) -> str:
    """passdb.tdb's modification time (stat %y, in nanoseconds): a time, or the step fails, so that
    a comparison never passes on two answers that are not one."""
    value = c.device.facts()["passdb"]
    c.expect(re.match(r"\d{4}-\d\d-\d\d \d", value or ""), f"passdb.tdb's time could not be read: {value!r}")
    return value


def smb_check(c: Check) -> None:
    with c.step("a pool is mounted read-write and managed by UNOS") as s:
        pool = c.pick_pool()
        root = pool["mountpoint"]
        s.text += f": {pool['name']} at {root}"
    user = share = c.run
    folder = f"{root}/{c.run}"
    password, newer, wrong = c.secret(), c.secret(), c.secret()

    def state() -> dict:
        facts = c.device.facts()
        return {
            "the users": sorted(f"{u['username']} {'admin' if u['is_admin'] else (u.get('smb') or {}).get('state')}"
                                for u in c.get("/auth/users")),
            "the shares": sorted(f"{s['name']} {s['path']} " + ",".join(f"{a['username']}:{a['level']}" for a in s["access"])
                                 for s in c.get("/shares/smb")),
            "smb.conf": facts["sha256"].get(SMB_CONF),
            "the account files": {f: facts["sha256"].get(f) for f in ACCOUNT_FILES},
            "the SMB host accounts": facts["accounts"],
            "the group unos-smb": facts["group"],
            "Samba's accounts": c.device.passdb_entries()[0],
        }

    with c.step("the device holds nothing of an earlier check"):
        c.baseline(state)

    with c.step(f"Samba runs UNOS's smb.conf without problems with a password history of 3 and a lock of "
                f"{LOCKOUT_MINUTES} minutes after {LOCKOUT_ATTEMPTS} failed sign-ins, and the device has smbclient, "
                "smbpasswd, stdbuf, testparm and pdbedit"):
        status = c.get("/shares/smb-status")
        c.expect(not status.get("problems"), "the Shares page reports: " + "; ".join(status.get("problems") or []))
        facts = c.device.facts()
        missing = sorted({"smbclient", "smbpasswd", "stdbuf", "testparm", "pdbedit", "getent"} - set(facts["tools"]))
        c.expect(not missing, f"the device lacks {', '.join(missing)} (installing one is an owner action)")
        c.expect(facts["testparm"], "testparm refuses smb.conf")
        # As UNOS's reconcile sets them (ADR-0278, ADR-0334): each failed sign-in but a retry of one of the
        # two previous passwords then counts, which writes passdb.tdb, and the steps with wrong passwords
        # compare it.
        policies = c.device.passdb_entries()[1]
        for key, (name, value) in SAMBA_POLICIES.items():
            c.expect(policies[key] == str(value), f"Samba's {name} is {policies[key]!r}, not {value} "
                                                  "(ADR-0278, ADR-0334)")
        since, sizes = facts["now"], facts["sizes"]
        guests = status.get("guest_shares") or []

    with c.step("no administrator has a Samba account or a marked host account"):
        admins = [u["username"] for u in c.get("/auth/users") if u["is_admin"]]
        names = set(c.device.passdb_entries()[0])
        accounts = {line.split(":")[0] for line in c.device.facts()["accounts"]}
        c.expect(not (set(admins) & (names | accounts)), f"{sorted(set(admins) & (names | accounts))} have one")

    with c.step(f"a new user {user} with SMB on gets a marked host account and a Samba account"):
        c.on_cleanup(f"delete the user {user}", lambda: c.delete_user_named(user))
        made = c.post("/auth/users", {"username": user, "password": password, "smb_enabled": True})
        uid = made["id"]
        c.expect((made.get("smb") or {}).get("state") == "active",
                 f"its SMB state is {(made.get('smb') or {}).get('state')}: {made.get('warnings')}")
        marked_account(c, user)

    with c.step("the new user's share list holds no share"):
        listing = c.device.smb_list(user, password)
        c.expect(listing.ok, f"smbclient -L failed: {listing.status}")
        c.expect(not listing.disks, f"it lists {listing.disks}")

    def grant(level: str | None) -> None:
        version = c.get(f"/shares/smb/{share}")["version"]
        access = [] if level is None else [{"user_id": uid, "username": user, "level": level}]
        c.call("PATCH", f"/shares/smb/{share}", {"expected_version": version, "access": access})

    with c.step(f"the share {share} on a new folder makes the folder 2770 root:unos-smb, and Samba serves it"):
        c.on_cleanup(f"delete {folder} for good", lambda: c.delete_for_good(folder))
        c.on_cleanup(f"delete the share {share}", lambda: c.delete_share(share))
        made = c.post("/shares/smb", {"name": share, "path": folder,
                                      "access": [{"user_id": uid, "username": user, "level": "write"}]})
        c.expect(made.get("served") and made.get("available"), f"it is served {made.get('served')}, "
                                                              f"available {made.get('available')}: {made.get('path_problem')}")
        stat_is(c, c.device.stat([folder]), folder, "2770 root:unos-smb")
        conf = c.device.read(SMB_CONF) or ""
        c.expect(conf.startswith(SMB_HEADER) and f"\n[{share}]\n" in conf, "smb.conf lacks UNOS's header or the section")
        c.expect(c.device.facts()["testparm"], "testparm refuses smb.conf")

    with c.step("a share of the pool's top folder or of a folder inside a share is refused"):
        for other, path in ((share + "x", root), (share + "y", f"{folder}/inner")):
            c.on_cleanup(f"delete the share {other}", lambda other=other: c.delete_share(other))
            c.post("/shares/smb", {"name": other, "path": path}, expect=(400, 409))

    with contextlib.ExitStack() as held:
        with c.step("in a signed session the user writes a file and a folder, which get the group unos-smb"):
            smb = held.enter_context(c.device.smb_session(user, password, share))
            done = smb.run("put data.txt a.txt", "mkdir d")
            c.expect(done.ok, f"smbclient: {done.status} {tail(done.output, 200)}")
            facts = c.device.stat([f"{folder}/a.txt", f"{folder}/d"])
            c.expect(facts[f"{folder}/a.txt"].endswith(":" + SMB_GROUP) and facts[f"{folder}/d"].startswith("2")
                     and facts[f"{folder}/d"].endswith(":" + SMB_GROUP), f"they are {facts}")

        with c.step("New Folder and Upload in the share's folder give 2770 root:unos-smb and 660 root:unos-smb"):
            c.post("/files/mkdir", {"path": folder, "name": "f"})
            c.upload(folder, "u.txt", DATA)
            facts = c.device.stat([f"{folder}/f", f"{folder}/u.txt"])
            stat_is(c, facts, f"{folder}/f", "2770 root:unos-smb")
            stat_is(c, facts, f"{folder}/u.txt", "660 root:unos-smb")

        with c.step("over SMB the user changes the uploaded file and puts a file into the new folder"):
            done = smb.run("put data2.txt u.txt", "put data.txt f/g.txt")
            c.expect(done.ok, f"smbclient: {done.status} {tail(done.output, 200)}")
            c.expect(c.device.read(f"{folder}/u.txt") == DATA2, "u.txt holds other bytes")
            c.expect(c.device.stat([f"{folder}/f/g.txt"])[f"{folder}/f/g.txt"] != "missing", "f/g.txt is missing")

        with c.step("lowering the access to read during the session makes its next write fail") as s:
            grant("read")
            done = smb.run("put data.txt b.txt")
            c.expect(done.status.startswith("NT_STATUS_"), f"the write answered {done.status}: {tail(done.output, 200)}")
            stat_is(c, c.device.stat([f"{folder}/b.txt"]), f"{folder}/b.txt", "missing")
            s.text += f": {done.status}"

    with contextlib.ExitStack() as held:
        with c.step("with read access the user lists and reads files, and a write is refused"):
            smb = held.enter_context(c.device.smb_session(user, password, share))
            done = smb.run("ls", "get a.txt got.txt", "!cat got.txt")
            c.expect(done.ok and DATA.strip() in done.output, f"smbclient: {done.status} {tail(done.output, 200)}")
            done = smb.run("put data.txt c.txt")
            c.expect(done.status == "NT_STATUS_ACCESS_DENIED", f"the write answered {done.status}")
            stat_is(c, c.device.stat([f"{folder}/c.txt"]), f"{folder}/c.txt", "missing")

        with c.step("a new comment is saved with the expected version, and a stale version answers 409"):
            version = c.get(f"/shares/smb/{share}")["version"]
            changed = c.call("PATCH", f"/shares/smb/{share}", {"expected_version": version, "comment": "unos scenario check"})
            c.expect(changed.get("comment") == "unos scenario check" and changed.get("version") != version,
                     f"the share is {changed}")
            c.call("PATCH", f"/shares/smb/{share}", {"expected_version": version, "comment": "stale"}, expect=409)

        with c.step("removing the user's access during the session ends its tree connect") as s:
            grant(None)
            done = smb.run("ls")
            c.expect(done.status.startswith("NT_STATUS_"), f"the session answered {done.status}: {tail(done.output, 200)}")
            s.text += f": {done.status}"

    with c.step("without access the user cannot connect to the share") as s:
        with c.device.smb_session(user, password, share) as smb:
            done = smb.run("ls")
        c.expect(done.ended and done.status in ("NT_STATUS_ACCESS_DENIED", "NT_STATUS_BAD_NETWORK_NAME"),
                 f"smbclient answered {done.status}: {tail(done.output, 200)}")
        s.text += f": {done.status}"

    with c.step("the user gets read-write access again"):
        grant("write")

    # shares.per-share-protocol-options (ADR-0377): a second share, of a folder in a new volume, whose
    # options are turned on and off in turn, each shown with sessions. Its snapshot goes into the
    # volume's .snapshots, which goes with the volume.
    volume, vshare = f"{c.run}v", f"{c.run}v"
    vpath = f"{root}/{volume}"
    vfolder = f"{vpath}/files"

    def options(**changes: bool) -> dict:
        version = c.get(f"/shares/smb/{vshare}")["version"]
        return c.call("PATCH", f"/shares/smb/{vshare}", {"expected_version": version, **changes})

    def section_now() -> dict[str, str]:
        return smb_section(c.device.read(SMB_CONF) or "", vshare) or {}

    with c.step(f"a share {vshare} of a new folder in a new volume {volume}, with Previous Versions on, lists the "
                f"volume's snapshots (shadow_copy2 with shadow:mountpoint = {vpath}), testparm takes smb.conf, and "
                "in a session the user writes a file there"):
        c.on_cleanup(f"delete the volume {volume}", lambda: c.delete_volume(pool["name"], volume))
        c.post(f"/storage/pools/{pool['name']}/volumes", {"name": volume})
        c.on_cleanup(f"delete the share {vshare}", lambda: c.delete_share(vshare))
        made = c.post("/shares/smb", {"name": vshare, "path": vfolder, "previous_versions": True,
                                      "access": [{"user_id": uid, "username": user, "level": "write"}]})
        c.expect(made.get("previous_versions") is True and made.get("served"), f"the share is {made}")
        section = section_now()
        c.expect("shadow_copy2" in section.get("vfs objects", "").split() and section.get("shadow:mountpoint") == vpath,
                 f"the section has vfs objects = {section.get('vfs objects')}, shadow:mountpoint = "
                 f"{section.get('shadow:mountpoint')}")
        c.expect(c.device.facts()["testparm"], "testparm refuses smb.conf")
        with c.device.smb_session(user, password, vshare) as smb:
            done = smb.run("put data.txt v.txt")
        c.expect(done.ok, f"smbclient: {done.status} {tail(done.output, 200)}")

    with c.step("a snapshot of the volume that the check makes on the Snapshots page, with the name it gives, is "
                "listed for that file as @GMT-<its time>, and once deleted no longer") as s:
        def delete_snapshots() -> None:
            """Every snapshot of the check's new volume is the check's, also one whose answer was lost."""
            found = c.get("/snapshots/", {"subvolume": vpath}, expect=(200, 404))
            for snapshot in found if isinstance(found, list) else []:
                c.call("DELETE", f"/snapshots/{snapshot['name']}", params={"subvolume": vpath}, expect=(200, 404))

        c.on_cleanup(f"delete the snapshots of {vpath}", delete_snapshots)
        snap = str(c.post("/snapshots/", {"subvolume": vpath}).get("name") or "")
        token = gmt_token(snap)
        c.expect(token is not None, f"the Snapshots page named the snapshot {snap!r}, which Previous Versions "
                                    "do not list (shadow:snapprefix, shadow:format)")
        with c.device.smb_session(user, password, vshare) as smb:
            listed = smb.run("allinfo v.txt")
        c.expect(listed.ok and token in listed.output.split(),
                 f"allinfo v.txt answered {listed.status} without {token}: {tail(listed.output, 300)}")
        c.call("DELETE", f"/snapshots/{snap}", params={"subvolume": vpath})
        with c.device.smb_session(user, password, vshare) as smb:
            done = smb.run("allinfo v.txt")
        c.expect(done.ok and token not in done.output.split(),
                 f"after the snapshot's Delete allinfo v.txt answered {done.status}: {tail(done.output, 300)}")
        s.text += f": {token}"

    with c.step("with encryption required the share's section says so and testparm takes smb.conf; the open "
                "sessions of an SMB 2.1 client and of an SMB3 client that does not encrypt end, a new SMB 2.1 client, "
                "which cannot encrypt, is refused, a client that encrypts (SMB3) lists the share, the SMB 2.1 client "
                f"still opens {share}, and with the option off the section asks for Samba's default again and the "
                "SMB 2.1 client gets in") as s:
        # A tree connect keeps the encryption it was granted: only closing it (ADR-0103) ends the
        # unencrypted sessions that were open before.
        with c.device.smb_session(user, password, vshare, protection="smb2") as older, \
                c.device.smb_session(user, password, vshare) as plain:
            clients = ((older, "SMB 2.1"), (plain, "SMB3 without encryption"))
            for session, client in clients:
                done = session.run("ls")
                c.expect(done.ok, f"before the change the {client} client answered {done.status}: "
                                  f"{tail(done.output, 200)}")
            options(encryption_required=True)
            line = section_now().get("smb encrypt")
            c.expect(line == "required", f"the section has smb encrypt = {line}")
            c.expect(c.device.facts()["testparm"], "testparm refuses smb.conf")
            for session, client in clients:
                done = session.run("ls")
                c.expect(done.ended or done.status.startswith("NT_STATUS_"),
                         f"the open session of the {client} client still lists the share: {tail(done.output, 200)}")
        with c.device.smb_session(user, password, vshare, protection="smb2") as smb:
            refused = smb.run("ls")
        c.expect(not refused.ok and refused.status == "NT_STATUS_ACCESS_DENIED",
                 f"the SMB 2.1 client answered {refused.status}: {tail(refused.output, 200)}")
        with c.device.smb_session(user, password, vshare, protection="encrypt") as smb:
            done = smb.run("ls")
        c.expect(done.ok and "v.txt" in done.output, f"the encrypting client answered {done.status}: "
                                                     f"{tail(done.output, 200)}")
        # The requirement is the share's own: [global] keeps Samba's default for every other share.
        with c.device.smb_session(user, password, share, protection="smb2") as smb:
            done = smb.run("ls")
        c.expect(done.ok, f"the SMB 2.1 client answered {done.status} on {share}, which does not require encryption: "
                          f"{tail(done.output, 200)}")
        options(encryption_required=False)
        line = section_now().get("smb encrypt")
        c.expect(line == "default", f"with the option off the section has smb encrypt = {line}")
        with c.device.smb_session(user, password, vshare, protection="smb2") as smb:
            done = smb.run("ls")
        c.expect(done.ok, f"with the option off the SMB 2.1 client answered {done.status}: {tail(done.output, 200)}")
        s.text += f": {refused.status}"

    def advertised(answer: dict, change: str) -> None:
        """After a change of the Time Machine shares UNOS restarts avahi-daemon, so that smbd announces
        them (ADR-0377); a restart that failed is a warning of the answer."""
        c.expect(not answer.get("warnings"), f"{change} warns: " + "; ".join(answer.get("warnings") or []))

    with c.step("with Time Machine too, the section's one vfs objects line loads catia, fruit and streams_xattr "
                "before the snapshots' modules, the section offers itself to Time Machine, [global] turns Apple's "
                "extensions on without NFS ACEs, testparm takes smb.conf, the answers warn of no failed restart "
                "of avahi-daemon and Avahi resolves the share in the device's _adisk._tcp service; with both off "
                "the line is empty and the service no longer names the share (a Mac's backup is a step by hand)") as s:
        advertised(options(time_machine=True), "turning Time Machine on")
        announced = adisk_until(c, vshare, listed=True)
        conf = c.device.read(SMB_CONF) or ""
        section, glob = smb_section(conf, vshare) or {}, smb_global(conf)
        c.expect(section.get("vfs objects") == "catia fruit streams_xattr shadow_copy2 btrfs"
                 and section.get("fruit:time machine") == "yes",
                 f"the section has vfs objects = {section.get('vfs objects')}, fruit:time machine = "
                 f"{section.get('fruit:time machine')}")
        c.expect(glob.get("fruit:aapl") == "yes" and glob.get("fruit:nfs_aces") == "no",
                 f"[global] has fruit:aapl = {glob.get('fruit:aapl')}, fruit:nfs_aces = {glob.get('fruit:nfs_aces')}")
        c.expect(c.device.facts()["testparm"], "testparm refuses smb.conf")
        advertised(options(time_machine=False, previous_versions=False), "turning Time Machine off")
        adisk_until(c, vshare, listed=False)
        s.text += f": _adisk._tcp named {', '.join(announced)}"
        conf = c.device.read(SMB_CONF) or ""
        section = smb_section(conf, vshare) or {}
        others = [x["name"] for x in c.get("/shares/smb") if x.get("time_machine")]
        c.expect(section.get("vfs objects") == ("catia fruit streams_xattr" if others else "")
                 and "fruit:time machine" not in section,
                 f"with both off the section has vfs objects = {section.get('vfs objects')}, fruit:time machine = "
                 f"{section.get('fruit:time machine')}")
        if others:
            s.text += f"; the device's own Time Machine shares ({', '.join(others)}) keep fruit in every section"
        else:
            c.expect(not [key for key in smb_global(conf) if key.startswith("fruit:")],
                     "[global] keeps fruit lines without a Time Machine share")

    with c.step(f"Delete of the share {vshare} and of the volume {volume}"):
        c.call("DELETE", f"/shares/smb/{vshare}")
        c.call("DELETE", f"/storage/pools/{pool['name']}/volumes/{volume}")

    # auth.user-rename (ADR-0368): the user and its SMB account take a new name; the steps after these use it.
    renamed, renamed_password = f"{user}r", c.secret()
    with contextlib.ExitStack() as held:
        with c.step(f"renaming the user to {renamed} with a new password ends its open session; its marked host "
                    "account keeps its uid under the new name, and so do the files it wrote, and Samba has an "
                    "account of the new name and none of the old") as s:
            uids = {line.split(":")[0]: line.split(":")[2] for line in c.device.facts()["accounts"]}
            smb = held.enter_context(c.device.smb_session(user, password, share))
            done = smb.run("ls")
            c.expect(done.ok, f"the session did not start: {done.status}")
            c.on_cleanup(f"delete the user {renamed}", lambda: c.delete_user_named(renamed))
            changed = c.call("PUT", f"/auth/users/{uid}",
                             {"username": user, "new_username": renamed, "password": renamed_password})
            found = (changed.get("smb") or {}).get("state")
            c.expect(changed.get("username") == renamed and found == "active",
                     f"the user is {changed.get('username')} with SMB {found}: {changed.get('warnings')}")
            done = smb.run("ls")
            c.expect(done.ended or done.status.startswith("NT_STATUS_"), f"the session answered: {tail(done.output, 200)}")
            now = {line.split(":")[0]: line.split(":")[2] for line in c.device.facts()["accounts"]}
            c.expect(user not in now, f"getent passwd still has {user}")
            marked_account(c, renamed)
            c.expect(now.get(renamed) == uids.get(user), f"{renamed} has uid {now.get(renamed)}, not {uids.get(user)}")
            owner = c.device.stat([f"{folder}/a.txt"])[f"{folder}/a.txt"]
            c.expect(owner.endswith(f" {renamed}:{SMB_GROUP}"), f"a.txt, written by {user}, is {owner}")
            c.expect(user not in c.device.passdb_entries()[0], f"pdbedit -L still lists {user}")
            s.text += f": {done.status}"
    # The old name keeps its own password: Samba's account of the old name, had it stayed, would take it.
    old, old_password, user, password = user, password, renamed, renamed_password

    with c.step("the share's access follows the new name: the Shares API and smb.conf's section name it, and in a "
                "signed session it writes in the share"):
        access = [(a["username"], a["level"]) for a in c.get(f"/shares/smb/{share}")["access"]]
        c.expect(access == [(user, "write")], f"the share's access is {access}")
        section = smb_section(c.device.read(SMB_CONF) or "", share) or {}
        c.expect(section.get("valid users") == user and section.get("write list") == user,
                 f"valid users = {section.get('valid users')}, write list = {section.get('write list')}")
        with c.device.smb_session(user, password, share) as smb:
            done = smb.run("put data.txt renamed.txt")
        c.expect(done.ok, f"smbclient: {done.status} {tail(done.output, 200)}")
        owner = c.device.stat([f"{folder}/renamed.txt"])[f"{folder}/renamed.txt"]
        c.expect(owner.endswith(f" {user}:{SMB_GROUP}"), f"renamed.txt is {owner}")

    with c.step(f"UNOS signs the user in as {user} and refuses {old}, and Samba refuses {old}") as s:
        mine = c.api.as_user(user, password, USER_TOKEN.format(c.run))
        c.on_cleanup(f"delete the token of {user} in the device's RAM", mine.forget_token)
        me = c.get("/auth/me", api=mine)
        c.expect(me.get("username") == user, f"the token is of {me}")
        theirs = c.api.as_user(old, old_password, USER_TOKEN.format(c.run + "old"))
        c.on_cleanup(f"delete the token of {old} in the device's RAM", theirs.forget_token)
        try:
            refused = f"it signed in as {c.get('/auth/me', api=theirs).get('username')}"
        except relay.ApiError as error:
            refused = str(error)
        c.expect(refused.endswith("failed: HTTP 401"), f"the sign-in as {old}: {refused}")
        if guests:  # Samba takes a user without a Samba account as a guest (ADR-0339)
            s.text += f"; Samba's refusal of {old} is not tried, as the device has guest shares"
        else:
            listing = c.device.smb_list(old, old_password)
            c.expect(not listing.ok and listing.status == "NT_STATUS_LOGON_FAILURE", f"{old} answered {listing.status}")
            s.text += f": Samba answered {old} {listing.status}"

    with contextlib.ExitStack() as held:
        with c.step("a new password set by an administrator ends the user's open session") as s:
            smb = held.enter_context(c.device.smb_session(user, password, share))
            done = smb.run("ls")
            c.expect(done.ok, f"the session did not start: {done.status}")
            c.call("PUT", f"/auth/users/{uid}", {"password": newer, "username": user})
            done = smb.run("ls")
            c.expect(done.ended or done.status.startswith("NT_STATUS_"), f"the session answered: {tail(done.output, 200)}")
            s.text += f": {done.status}"

    with c.step("the replaced password is refused with NT_STATUS_LOGON_FAILURE and leaves passdb.tdb as it was"):
        before = passdb_time(c)
        listing = c.device.smb_list(user, password)
        c.expect(not listing.ok and listing.status == "NT_STATUS_LOGON_FAILURE", f"it answered {listing.status}")
        after = c.device.facts()["passdb"]
        c.expect(after == before, f"passdb.tdb changed at {after} (Samba's password history: "
                                  f"{c.device.passdb_entries()[1]['history']})")

    with c.step("the new password signs in"):
        listing = c.device.smb_list(user, newer)
        c.expect(listing.ok and share in listing.disks, f"smbclient -L: {listing.status}, {listing.disks}")

    # Before the steps with wrong passwords: no failure is counted, so a refused change writes nothing.
    with c.step("the user's own change of that password through SAMR (smbpasswd -r, which stands in for Windows' "
                "Change a password) is refused with NT_STATUS_PASSWORD_RESTRICTION, leaves passdb.tdb as it was, "
                "and the password still signs in (ADR-0342)") as s:
        before = passdb_time(c)
        change = c.device.smb_change_password(user, newer, c.secret())
        c.expect(not change.ok and change.status == "NT_STATUS_PASSWORD_RESTRICTION",
                 f"smbpasswd -r answered {change.status}: {tail(change.output, 200)}")
        after = passdb_time(c)
        c.expect(after == before, f"passdb.tdb changed at {after}")
        listing = c.device.smb_list(user, newer)
        c.expect(listing.ok, f"the password answered {listing.status} after the refused change")
        s.text += f": {tail(change.output, 120)}"

    with c.step("a wrong password is refused with NT_STATUS_LOGON_FAILURE, never as a guest, and changes passdb.tdb, "
                "where Samba counts it (ADR-0334)") as s:
        before = passdb_time(c)
        listing = c.device.smb_list(user, wrong)
        c.expect(not listing.ok and listing.status == "NT_STATUS_LOGON_FAILURE", f"it answered {listing.status}")
        after = passdb_time(c)
        c.expect(after != before, f"passdb.tdb is as it was ({after}): Samba did not count the failure")
        if guests:  # restrict anonymous = 1 and map to guest = Bad User: guests may connect (ADR-0339)
            s.text += "; a null session is not tried, as the device has guest shares"
        else:
            listing = c.device.smb_anonymous()
            c.expect(not listing.ok and not listing.disks and listing.status in ("NT_STATUS_ACCESS_DENIED", "NT_STATUS_LOGON_FAILURE"),
                     f"the null session answered {listing.status} and listed {listing.disks}")
            s.text += f", and an anonymous (null) session is refused: {listing.status}"

    # The last step that signs in: the account stays locked for LOCKOUT_MINUTES, longer than the
    # steps after this one and the clean-up take, and is deleted while it is locked.
    with c.step(f"{LOCKOUT_ATTEMPTS} wrong passwords in a row lock the account: the right password is then refused "
                "with NT_STATUS_ACCOUNT_LOCKED_OUT, which leaves passdb.tdb as it was (ADR-0334)"):
        listing = c.device.smb_list(user, newer)  # sets the count of the step before back to 0
        c.expect(listing.ok, f"the right password answered {listing.status} before the wrong ones")
        for n in range(1, LOCKOUT_ATTEMPTS + 1):
            listing = c.device.smb_list(user, c.secret())
            c.expect(not listing.ok and listing.status == "NT_STATUS_LOGON_FAILURE",
                     f"wrong password {n} of {LOCKOUT_ATTEMPTS} answered {listing.status}")
        before = passdb_time(c)
        listing = c.device.smb_list(user, newer)
        c.expect(not listing.ok and listing.status == "NT_STATUS_ACCOUNT_LOCKED_OUT",
                 f"after {LOCKOUT_ATTEMPTS} wrong passwords the right one answered {listing.status}")
        after = passdb_time(c)
        c.expect(after == before, f"passdb.tdb changed at {after} while the account was locked")

    if guests:
        c.skip("with no guest share, signing is mandatory and guests are off", f"the device has guest shares: {guests}")
    else:
        with c.step("with no guest share, server signing is mandatory and the guest settings are off"):
            conf = smb_global(c.device.read(SMB_CONF) or "")
            for key, want in (("server signing", "mandatory"), ("map to guest", "Never"), ("restrict anonymous", "2")):
                c.expect(conf.get(key) == want, f"{key} = {conf.get(key)}, not {want}")

    with c.step(f"Samba logs to {SAMBA_RUN_LOG} with a size cap, and its logs in /var/log/samba did not grow"):
        conf = smb_global(c.device.read(SMB_CONF) or "")
        c.expect(conf.get("log file") == SAMBA_RUN_LOG and conf.get("max log size") == "100",
                 f"log file = {conf.get('log file')}, max log size = {conf.get('max log size')}")
        now = c.device.facts()["sizes"]
        c.expect(now.get(SAMBA_RUN_LOG) != "missing", f"{SAMBA_RUN_LOG} is missing")
        grown = [f for f in SAMBA_LOGS if now.get(f) != sizes.get(f)]
        c.expect(not grown, f"{grown} changed: {[sizes.get(f) for f in grown]} to {[now.get(f) for f in grown]}")

    with c.step("no password of the check is in an answer, the supervisor's log, the journal, Samba's log or the audit log"):
        c.scanned(since, ("the supervisor's log", "the journal", "Samba's log", "the audit log"))

    with c.step("Delete removes the share's section from smb.conf and keeps its folder with its files"):
        c.call("DELETE", f"/shares/smb/{share}")
        conf_text = c.device.read(SMB_CONF) or ""
        c.expect(f"[{share}]" not in conf_text, "smb.conf still has the section")
        c.expect(c.device.read(f"{folder}/a.txt") == DATA, "a.txt is gone")

    with c.step("deleting the user removes its host account and its Samba account"):
        c.delete_user(uid, user)
        c.expect(not [line for line in c.device.facts()["accounts"] if line.split(":")[0] == user],
                 "getent passwd still has it")
        c.expect(user not in c.device.passdb_entries()[0], "pdbedit -L still lists it")


# --- smb.conf by hand ------------------------------------------------------------------------


def copied_problem(section: str, copy: str) -> str:
    """The start of the Shares page's problem after an apply copied SECTION to COPY (ADR-0362)."""
    return f"smb.conf had shares that UNOS did not create ({section}); they are kept in {copy}"


def foreign_sections(conf: str) -> list[str]:
    """The share sections of smb.conf without UNOS's fixed lines: those that UNOS did not write."""
    want = {("".join(key.split()).lower(), value) for key, value in UNOS_SHARE_LINES}
    sections: dict[str, set] = {}
    name = None
    for line in conf.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1]
            sections.setdefault(name, set())
        elif name is not None and "=" in line and not line.startswith(("#", ";")):
            key, _, value = line.partition("=")
            sections[name].add(("".join(key.split()).lower(), value.strip()))
    return [n for n, lines in sections.items() if n.lower() not in NOT_SHARES and not want <= lines]


def problems_of(c: Check) -> list[str]:
    return c.get("/shares/smb-status")["problems"]


def wait_for_smb_lock(c: Check) -> None:
    """Wait until no change of SMB state runs, such as a restore whose answer was lost, which
    holds smb_lock to the end of its reconcile: a change of a share that does not exist waits for
    it, answers 404 and changes nothing."""
    c.call("PATCH", f"/shares/smb/{c.run}none", {}, expect=404)


def restart_and_wait(c: Check) -> int:
    """Restart the supervisor, and wait for its startup reconcile, which holds smb_lock from
    before the first request (src/main.py). The seconds until the container was healthy."""
    took = c.device.restart_supervisor()
    wait_for_smb_lock(c)
    return took


def restart_if_named(c: Check, section: str) -> None:
    """The copy's problem stays until the supervisor restarts (ADR-0362): restart it when the
    Shares page still names the check's section."""
    if any(section in p for p in problems_of(c)):
        restart_and_wait(c)
        left = [p for p in problems_of(c) if section in p]
        c.expect(not left, f"after a restart the Shares page still reports {left}")


def smbconf_check(c: Check) -> None:
    with c.step("a pool is mounted read-write and managed by UNOS") as s:
        pool = c.pick_pool()
        root = pool["mountpoint"]
        s.text += f": {pool['name']} at {root}"
    share = c.run
    hand = c.run + "hand"
    folder = f"{root}/{c.run}"
    new_folder = f"{folder}-new"
    edited: list[str] = []  # the digests of the hand-edited files, once they are known

    def state() -> dict:
        files = c.device.samba_files()
        return {
            "the users": sorted(u["username"] for u in c.get("/auth/users")),
            "the shares": sorted(f"{s['name']} {s['path']} {s['comment']!r} "
                                 + ",".join(f"{a['username']}:{a['level']}" for a in s["access"])
                                 for s in c.get("/shares/smb")),
            "the pool's top folder": sorted(i["name"] for i in c.get("/files/list", params={"path": root})["items"]),
            "smb.conf": (files.get("smb.conf") or {}).get("sha256"),
            "the files of /etc/samba": sorted(files),
            "the problems on the Shares page": problems_of(c),
        }

    def remove_share() -> None:
        c.delete_share(share)
        c.delete_for_good(folder)
        c.delete_for_good(new_folder)

    with c.step("the device holds nothing of an earlier check"):
        c.baseline(state)
    assert c.before is not None
    found = c.before["smb.conf"]

    earlier = sorted(name for name in c.before["the files of /etc/samba"] if REPLACED_COPY_NAME.fullmatch(name))
    with c.step(f"smb.conf is UNOS's, the Shares page reports no problem, and /etc/samba keeps fewer than "
                f"{REPLACED_COPIES_KEPT - 1} copies {REPLACED_COPY}, so that the check's two copies make UNOS delete "
                "none") as s:
        reported = c.before["the problems on the Shares page"]
        c.expect(not reported, "the Shares page reports: " + "; ".join(reported))
        conf = c.device.read(SMB_CONF) or ""
        c.expect(conf.startswith(SMB_HEADER), "smb.conf does not start with UNOS's header")
        others = foreign_sections(conf)
        c.expect(not others, f"smb.conf holds sections that UNOS did not write ({', '.join(others)}): an apply would "
                             f"copy them into the check's {REPLACED_COPY}, which its clean-up deletes")
        c.expect(len(earlier) < REPLACED_COPIES_KEPT - 1,
                 f"/etc/samba keeps {len(earlier)} copies {REPLACED_COPY}: the check's two copies would make UNOS "
                 f"delete the oldest, {earlier[0] if earlier else ''}")
        s.text += f": {len(earlier)} earlier copies"

    with c.step("a regular user for an access entry") as s:
        regular = [u for u in c.get("/auth/users")
                   if not u["is_admin"] and re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", u["username"])]
        if regular:  # one that exists: no writes for a user of the check's own (ADR-0214)
            uid, user = regular[0]["id"], regular[0]["username"]
            s.text += f": {user}"
        else:
            user = c.run
            c.on_cleanup(f"delete the user {user}", lambda: c.delete_user_named(user))
            uid = c.post("/auth/users", {"username": user, "password": c.secret(), "smb_enabled": False})["id"]
            s.text += f": {user}, made without SMB, as the device has none"
    entry = [{"user_id": uid, "username": user, "level": "read"}]

    with c.step(f"smb.conf edited by hand: UNOS's header replaced, and a share section [{hand}] appended that Samba "
                "does not serve (available = no)"):
        c.on_cleanup("restart the supervisor if the Shares page still names the check's section",
                     lambda: restart_if_named(c, hand))
        c.on_cleanup(f"put smb.conf back if it is not UNOS's, and delete the check's copies {REPLACED_COPY}",
                     lambda: c.device.hand_edit("restore", hand, *edited))
        edited.append(c.device.hand_edit("edit", hand, found or "")[0])
        text = c.device.read(SMB_CONF) or ""
        c.expect(not text.startswith(SMB_HEADER) and f"\n[{hand}]\n" in text, "smb.conf is not as edited")

    with c.step("before an apply, the Shares page names smb.conf as not UNOS's (ADR-0249)"):
        problems = problems_of(c)
        c.expect(len(problems) == 1 and problems[0].startswith(CONF_NOT_UNOS), f"it reports {problems}")

    with c.step(f"a new share {share} applies smb.conf: the hand-edited file goes byte for byte into a new "
                f"{REPLACED_COPY}, the earlier copies stay (ADR-0362), and smb.conf is UNOS's again, with the share's "
                f"section and without [{hand}]") as s:
        c.on_cleanup(f"delete the share {share} and its folders", remove_share)
        made = c.post("/shares/smb", {"name": share, "path": folder})
        c.expect(made.get("served") and made.get("version") == 1,
                 f"it is served {made.get('served')}, version {made.get('version')}: {made.get('path_problem')}")
        files = c.device.samba_files()
        new = sorted(name for name in files if REPLACED_COPY_NAME.fullmatch(name) and name not in earlier)
        c.expect(len(new) == 1 and files[new[0]]["sha256"] == edited[0],
                 f"the new copies are {', '.join(new) or 'none'}, not one with the edited file")
        copy = new[0]
        gone = [name for name in earlier if name not in files]
        c.expect(not gone, f"the apply deleted the earlier copies {', '.join(gone)}")
        s.text += f": {copy}"
        text = c.device.read(SMB_CONF) or ""
        c.expect(text.startswith(SMB_HEADER) and f"\n[{share}]\n" in text and f"[{hand}]" not in text,
                 "smb.conf is not UNOS's rendering with the share's section")
        c.expect(c.device.facts()["testparm"], "testparm refuses smb.conf")
        stat_is(c, c.device.stat([folder]), folder, "2770 root:unos-smb")
        applied = files["smb.conf"]["sha256"]

    with c.step("the Shares page names the copied section, its copy and the earlier copies, until the supervisor "
                "restarts"):
        problems = problems_of(c)
        c.expect(len(problems) == 1 and problems[0].startswith(copied_problem(hand, copy))
                 and all(name in problems[0] for name in earlier), f"it reports {problems}")
        copied = problems[0]

    with c.step("a new comment is saved with the expected version, and a stale version answers 409"):
        changed = c.call("PATCH", f"/shares/smb/{share}", {"expected_version": 1, "comment": "unos scenario check"})
        c.expect(changed.get("comment") == "unos scenario check" and changed.get("version") != 1, f"the share is {changed}")
        c.call("PATCH", f"/shares/smb/{share}", {"expected_version": 1, "comment": "stale"}, expect=409)
        applied = c.device.samba_files()["smb.conf"]["sha256"]

    with c.step(f"smb.conf edited by hand again, and a new comment applies it: the file goes byte for byte into a "
                f"second {REPLACED_COPY}, the check's first copy keeps its bytes, and the earlier copies stay") as s:
        edited.append(c.device.hand_edit("again", hand, applied)[0])
        changed = c.call("PATCH", f"/shares/smb/{share}",
                         {"expected_version": changed["version"], "comment": "unos scenario check, edited again"})
        files = c.device.samba_files()
        c.expect((files.get(copy) or {}).get("sha256") == edited[0], f"the apply replaced the check's first copy {copy}")
        new = sorted(name for name in files if REPLACED_COPY_NAME.fullmatch(name) and name not in earlier + [copy])
        c.expect(len(new) == 1 and files[new[0]]["sha256"] == edited[1],
                 f"the new copies are {', '.join(new) or 'none'}, not one with the file edited again")
        second = new[0]
        gone = [name for name in earlier if name not in files]
        c.expect(not gone, f"the apply deleted the earlier copies {', '.join(gone)}")
        s.text += f": {second}"
        text = c.device.read(SMB_CONF) or ""
        c.expect(text.startswith(SMB_HEADER) and f"\n[{share}]\n" in text and f"[{hand}]" not in text,
                 "smb.conf is not UNOS's rendering with the share's section")
        applied = files["smb.conf"]["sha256"]

    with c.step("the Shares page names the section in the second copy, and names the check's first copy as an earlier "
                "copy beside the others"):
        problems = problems_of(c)
        named = problems[0].partition(REPLACED_EARLIER)[2] if len(problems) == 1 else ""
        c.expect(len(problems) == 1 and problems[0].startswith(copied_problem(hand, second)) and copy in named
                 and all(name in named for name in earlier), f"it reports {problems}")
        copied = problems[0]

    with c.step("with its folder replaced by a symbolic link on the host, the share is not served, and the Shares page "
                "says why") as s:
        c.on_cleanup(f"put the folder {folder} back in place of its symbolic link", lambda: c.expect(
            c.device.unserve("restore", folder) != "link", f"{folder} is still a symbolic link"))
        c.expect(c.device.unserve("link", folder) == "link", f"{folder} is not a symbolic link")
        info = c.get(f"/shares/smb/{share}")
        problem = info.get("path_problem")
        c.expect(info.get("served") is False and problem, f"it is served {info.get('served')}: {problem}")
        problems = problems_of(c)
        c.expect(sorted(problems) == sorted([copied, f"Share {share} is not served: {problem}"]), f"it reports {problems}")
        version = info["version"]
        s.text += f": {problem}"

    with c.step("access without a new folder is refused with 400 and that reason, and changes nothing"):
        refused = c.call("PATCH", f"/shares/smb/{share}", {"expected_version": version, "access": entry}, expect=400)
        c.expect(isinstance(refused, dict) and refused.get("detail") == problem, f"it answered {refused}")
        info = c.get(f"/shares/smb/{share}")
        c.expect(info["version"] == version and info["access"] == [], f"the share is {info}")
        c.expect(c.device.samba_files()["smb.conf"]["sha256"] == applied, "smb.conf changed")

    with c.step(f"access together with a new folder is saved, {new_folder} is made 2770 root:unos-smb, and the share is "
                "served there"):
        changed = c.call("PATCH", f"/shares/smb/{share}", {"expected_version": version, "path": new_folder, "access": entry})
        c.expect(changed.get("served") and changed.get("path") == new_folder and changed.get("version") != version
                 and [(a["username"], a["level"]) for a in changed.get("access", [])] == [(user, "read")],
                 f"the share is {changed}")
        stat_is(c, c.device.stat([new_folder]), new_folder, "2770 root:unos-smb")
        text = c.device.read(SMB_CONF) or ""
        section = text.split(f"\n[{share}]\n", 1)[1].split("\n[", 1)[0] if f"\n[{share}]\n" in text else ""
        c.expect(f"path = {new_folder}\n" in section, f"smb.conf's section of the share: {section!r}")
        problems = problems_of(c)
        c.expect(problems == [copied], f"the Shares page reports {problems}")

    with c.step("Delete removes the share's section and keeps the folder with its files, and smb.conf is byte for byte "
                "as the check found it"):
        c.upload(new_folder, "kept.txt", DATA)
        c.call("DELETE", f"/shares/smb/{share}")
        c.expect(f"[{share}]" not in (c.device.read(SMB_CONF) or ""), "smb.conf still has the section")
        c.expect(c.device.read(f"{new_folder}/kept.txt") == DATA, "the folder's file is gone")
        c.expect(c.device.samba_files()["smb.conf"]["sha256"] == found, "smb.conf is not as the check found it")

    with c.step(f"with the check's two copies {REPLACED_COPY} deleted, a restart of the supervisor writes no file in "
                "/etc/samba, and the Shares page reports no problem") as s:
        left = c.device.hand_edit("restore", hand, *edited)[1]
        c.expect(sorted(left) == earlier, f"the copies {REPLACED_COPY} are {', '.join(sorted(left)) or 'none'}")
        before = c.device.samba_files()
        took = restart_and_wait(c)
        after = c.device.samba_files()
        c.expect(after == before, f"the restart {describe(before, after)}")
        problems = problems_of(c)
        c.expect(problems == [], f"the Shares page reports {problems}")
        s.text += f": healthy after about {took} s"


# --- shares: the sticky parent, Repair permissions, the user's own SMB, a guest share ---------

# How long the shares check waits for the end of its repair, which runs in the background
# (ADR-0363), and how often it asks: its share holds a few entries (the tests make both short).
REPAIR_WAIT = 300
REPAIR_POLL = 1


def repair_until_it_ends(c: Check, share: str, started: Any) -> tuple[dict, int]:
    """The run of Repair permissions of SHARE that started at STARTED once GET .../repair-permissions,
    the share's last finished run, answers it, and how many answers named the run before it (or none)."""
    deadline = time.monotonic() + REPAIR_WAIT
    before = 0
    while True:
        last = c.get(f"/shares/smb/{share}/repair-permissions", expect=(200, 404))
        if isinstance(last, dict) and last.get("started_at") == started:
            return last, before
        before += 1
        c.expect(time.monotonic() < deadline, f"the repair of {share} did not end within {REPAIR_WAIT} s")
        time.sleep(REPAIR_POLL)


def smb_state(answer: dict) -> str:
    """The state of GET /auth/me/smb's answer, with whether SMB is allowed and the code is asked."""
    account = answer["account"]
    return f"{account['state']}, allowed {account['allowed']}, two-factor code {answer['totp_required']}"


def shares_check(c: Check) -> None:
    """A guest share, a user's own SMB activation, Repair permissions and the sticky rule of
    share folders (ADR-0340)."""
    with c.step("a pool is mounted read-write and managed by UNOS") as s:
        pool = c.pick_pool()
        root = pool["mountpoint"]
        s.text += f": {pool['name']} at {root}"
    share = user = c.run
    folder = f"{root}/{c.run}"
    outside = f"{folder}-outside"
    sticky = f"{folder}-sticky"
    theirs = f"{sticky}/theirs"
    # The user's passwords: 7 characters, which UNOS allows a user without SMB; one with a tab; one
    # of 73 bytes, which UNOS refuses; and the one it keeps.
    short = c.secret(secrets.token_urlsafe(8)[:7])
    tabbed = c.secret(secrets.token_urlsafe(9) + "\t" + secrets.token_urlsafe(9))
    too_long = c.secret(secrets.token_urlsafe(60)[:UNOS_PASSWORD_BYTES + 1])
    password = c.secret()

    def state() -> dict:
        facts = c.device.facts()
        return {
            "the users": sorted(f"{u['username']} {'admin' if u['is_admin'] else (u.get('smb') or {}).get('state')}"
                                for u in c.get("/auth/users")),
            "the shares": sorted(f"{s['name']} {s['path']} guest {s['guest_ok']} "
                                 + ",".join(f"{a['username']}:{a['level']}" for a in s["access"])
                                 for s in c.get("/shares/smb")),
            "the pool's top folder": sorted(i["name"] for i in c.get("/files/list", params={"path": root})["items"]),
            "smb.conf": facts["sha256"].get(SMB_CONF),
            "the account files": {f: facts["sha256"].get(f) for f in ACCOUNT_FILES},
            "the SMB host accounts": facts["accounts"],
            "Samba's accounts": c.device.passdb_entries()[0],
            "the problems on the Shares page": problems_of(c),
        }

    with c.step("the device holds nothing of an earlier check"):
        c.baseline(state)

    with c.step("Samba runs UNOS's smb.conf without problems, and the device has smbclient, stdbuf, testparm, pdbedit "
                "and getent") as s:
        status = c.get("/shares/smb-status")
        c.expect(not status.get("problems"), "the Shares page reports: " + "; ".join(status.get("problems") or []))
        facts = c.device.facts()
        missing = sorted({"smbclient", "stdbuf", "testparm", "pdbedit", "getent"} - set(facts["tools"]))
        c.expect(not missing, f"the device lacks {', '.join(missing)} (installing one is an owner action)")
        c.expect(facts["testparm"], "testparm refuses smb.conf")
        since = facts["now"]
        acl = {"setfacl", "getfacl"} <= set(facts["tools"])
        guests = sorted(status.get("guest_shares") or [])
        s.text += ("; it has setfacl and getfacl" if acl else "; it has no setfacl or getfacl")
        s.text += f"; its guest shares: {', '.join(guests)}" if guests else "; it has no guest share"

    # shares.acl-aware-parent-check (ADR-0193): a share folder in a sticky folder.
    with c.step(f"over ssh on the pool: {sticky}, 1777 root:root, with a folder theirs of another account in it, "
                "1777 too") as s:
        c.on_cleanup(f"delete {sticky} for good", lambda: c.delete_for_good(sticky))
        other = c.device.entries("sticky", sticky)
        owner = f"{other['user']}:{other['group']}"
        facts = c.device.stat([sticky, theirs])
        stat_is(c, facts, sticky, "1777 root:root")
        stat_is(c, facts, theirs, f"1777 {owner}")
        s.text += f": {owner}, uid {other['uid']}"
    foreign = f"can be changed by other accounts (owner uid {other['uid']}, mode 1777)"
    excused = f"can be changed by other accounts (mode 1777, the share folder belongs to uid {other['uid']})"

    with c.step("a new share folder in theirs, a sticky folder of another account, is refused with 409 and the reason, "
                "and nothing is made") as s:
        c.on_cleanup(f"delete the share {share}n", lambda: c.delete_share(share + "n"))
        refused = c.post("/shares/smb", {"name": share + "n", "path": f"{theirs}/new"}, expect=409)
        detail = refused.get("detail") if isinstance(refused, dict) else refused
        c.expect(isinstance(detail, str) and f"{theirs} {foreign}" in detail, f"it answered {detail!r}")
        stat_is(c, c.device.stat([f"{theirs}/new"]), f"{theirs}/new", "missing")
        c.expect(share + "n" not in [x["name"] for x in c.get("/shares/smb")], "the share was saved")
        s.text += f": {detail}"

    with c.step("a share of theirs, a folder of another account in the sticky folder of root, is accepted with the "
                "warning that names its owner, the Shares page reports it, and Repair permissions refuses it with "
                "409; after its Delete the Shares page reports nothing") as s:
        c.on_cleanup(f"delete the share {share}t", lambda: c.delete_share(share + "t"))
        made = c.post("/shares/smb", {"name": share + "t", "path": theirs})
        warnings = made.get("warnings") or []
        c.expect(any(f"{sticky} {excused}" in w for w in warnings), f"its warnings are {warnings}")
        problems = problems_of(c)
        c.expect([p for p in problems if p.startswith(f"Share {share}t: {sticky} {excused}")],
                 f"the Shares page reports {problems}")
        refused = c.post(f"/shares/smb/{share}t/repair-permissions", expect=409)
        detail = refused.get("detail") if isinstance(refused, dict) else refused
        c.expect(isinstance(detail, str) and f"{sticky} {excused}" in detail, f"Repair permissions answered {detail!r}")
        stat_is(c, c.device.stat([theirs]), theirs, f"1777 {owner}")
        c.call("DELETE", f"/shares/smb/{share}t")
        problems = problems_of(c)
        c.expect(problems == [], f"the Shares page reports {problems}")
        s.text += f": {detail}"

    # shares.repair-permissions and shares.repair-in-background (ADR-0363, ADR-0156).
    with c.step(f"the share {share} on a new folder makes the folder 2770 root:unos-smb, and smb.conf gets its "
                "section, closed (available = no) while nobody has access"):
        c.on_cleanup(f"delete {folder} for good", lambda: c.delete_for_good(folder))
        c.on_cleanup(f"delete the share {share}", lambda: c.delete_share(share))
        made = c.post("/shares/smb", {"name": share, "path": folder})
        c.expect(made.get("served"), f"it is not served: {made.get('path_problem')}")
        c.expect(made.get("available") is False, f"available is {made.get('available')!r} for a share without access")
        stat_is(c, c.device.stat([folder]), folder, "2770 root:unos-smb")
        c.expect(f"\n[{share}]\n" in (c.device.read(SMB_CONF) or ""), "smb.conf lacks the share's section")

    names = {"user": other["user"], "group": other["group"]}
    entries = {f"{folder}/{name}": (before.format(**names), after.format(**names))
               for name, (before, after) in REPAIR_ENTRIES.items()}
    entries[f"{outside}/target.txt"] = ("644 root:root", "644 root:root")

    with c.step("over ssh in the share's folder: a folder and files of the group root with other modes, a file of "
                "another account for everyone, a private file and folder, a symbolic link to a file outside the "
                "share" + (", and an ACL entry for the other account" if acl else "")):
        c.on_cleanup(f"delete {outside} for good", lambda: c.delete_for_good(outside))
        c.device.entries("repair", folder, acl)
        facts = c.device.stat(sorted(entries))
        for path, (want, _) in entries.items():
            stat_is(c, facts, path, want)
        if acl:
            found = c.device.acl(f"{folder}/acl.txt")
            c.expect(f"user:{other['user']}:r--" in found and "mask::r--" in found, f"its ACL is {found}")

    with c.step("Repair permissions answers at once (202) that it runs, in the background (ADR-0363), and by its "
                "end, which the Shares API then answers as the last run, it gives them the group unos-smb, group "
                "read and write, and setgid on the folder, and keeps their owners") as s:
        started = c.post(f"/shares/smb/{share}/repair-permissions", expect=202)
        c.expect(isinstance(started, dict) and started.get("share") == share and started.get("state") == "running"
                 and started.get("finished_at") is None and started.get("started_at"), f"it answered {started}")
        result, before = repair_until_it_ends(c, share, started["started_at"])
        c.expect(result.get("state") == "done" and not result.get("errors"),
                 f"it ended {result.get('state')}: {result.get('message')} {result.get('error_samples')}")
        facts = c.device.stat(sorted(entries) + [folder])
        stat_is(c, facts, folder, "2770 root:unos-smb")
        for path, (want, after) in entries.items():
            if want != after:
                stat_is(c, facts, path, after)
        s.text += f": {result.get('message')} (GET named it after {before} earlier answer(s))"

    with c.step("it leaves the private file and folder, what the folder holds, the symbolic link and the file outside "
                "the share that the link points to as they were"):
        facts = c.device.stat(sorted(entries))
        for path, (want, after) in entries.items():
            if want == after:
                stat_is(c, facts, path, want)

    if acl:
        with c.step("the ACL entry stays, and the group permissions become the ACL's mask") as s:
            found = c.device.acl(f"{folder}/acl.txt")
            c.expect(f"user:{other['user']}:r--" in found and "group::r--" in found and "mask::rw-" in found,
                     f"its ACL is {found}")
            s.text += f": {', '.join(found)}"
    else:
        c.skip("the ACL entry stays, and the group permissions become the ACL's mask",
               "the device has no setfacl or getfacl (installing acl is an owner action)")

    with c.step("the Shares API shows the last run with its counts and errors") as s:
        last = c.get(f"/shares/smb/{share}/repair-permissions")
        c.expect(last == result, f"it shows {last}, not the run's {result}")
        skipped = result.get("skipped") or {}
        c.expect(result.get("visited") == REPAIR_VISITS + 1 and result.get("changed") == REPAIR_CHANGES
                 and skipped.get("symlink") == 1 and skipped.get("private") == 2 and not result.get("errors"),
                 f"its counts: visited {result.get('visited')}, changed {result.get('changed')}, skipped {skipped}, "
                 f"errors {result.get('errors')}")
        s.text += (f": visited {result['visited']}, changed {result['changed']}, skipped "
                   + ", ".join(f"{k} {v}" for k, v in skipped.items() if v) + f", errors {result['errors']}")

    with c.step("with its folder replaced by a symbolic link, the share is not served, and Repair permissions refuses "
                "it with 409 and the reason") as s:
        c.on_cleanup(f"put the folder {folder} back in place of its symbolic link", lambda: c.expect(
            c.device.unserve("restore", folder) != "link", f"{folder} is still a symbolic link"))
        c.expect(c.device.unserve("link", folder) == "link", f"{folder} is not a symbolic link")
        info = c.get(f"/shares/smb/{share}")
        problem = info.get("path_problem")
        c.expect(info.get("served") is False and problem, f"it is served {info.get('served')}: {problem}")
        refused = c.post(f"/shares/smb/{share}/repair-permissions", expect=409)
        detail = refused.get("detail") if isinstance(refused, dict) else refused
        c.expect(detail == f"Share {share} is not served: {problem}; change its folder first", f"it answered {detail!r}")
        c.expect(c.get(f"/shares/smb/{share}/repair-permissions") == result, "the last run is another")
        c.expect(c.device.unserve("restore", folder) == "folder", f"{folder} is not a folder again")
        c.expect(c.get(f"/shares/smb/{share}").get("served"), "the share is not served with its folder back")
        s.text += f": {problem}"

    # shares.self-activation (ADR-0118, ADR-0068, ADR-0122/0279, ADR-0131).
    with c.step(f"a regular user {user} without SMB, with a password of 7 characters, which UNOS's own rules allow, "
                "signs in with its own token, and the Account page's API says that SMB is off") as s:
        c.on_cleanup(f"delete the user {user}", lambda: c.delete_user_named(user))
        made = c.post("/auth/users", {"username": user, "password": short, "smb_enabled": False})
        uid = made["id"]
        mine = c.api.as_user(user, short, USER_TOKEN.format(c.run))
        c.on_cleanup(f"delete the token of {user} in the device's RAM", mine.forget_token)
        me = c.get("/auth/me", api=mine)
        c.expect(me.get("username") == user and me.get("is_admin") is False, f"the token is of {me}")
        found = smb_state(c.get("/auth/me/smb", api=mine))
        c.expect(found == "off, allowed False, two-factor code False", f"it says {found}")
        s.text += f": {found}"

    with c.step("an administrator allows SMB: the Account page's API says that it is allowed, without a two-factor "
                "code") as s:
        c.post(f"/auth/users/{uid}/smb/enable", {"username": user})
        found = smb_state(c.get("/auth/me/smb", api=mine))
        c.expect(found == "password_needed, allowed True, two-factor code False", f"it says {found}")
        s.text += f": {found}"

    with c.step("SMB refuses the user's password of 7 characters and then one with a tab, each with the reason; UNOS "
                f"itself refuses a new password over {UNOS_PASSWORD_BYTES} bytes (422), so that SMB's own limit of "
                f"{UNOS_PASSWORD_BYTES} bytes cannot be reached") as s:
        refused = c.post("/auth/me/smb/enable", {"current_password": short}, expect=400, api=mine)
        c.expect(refused.get("detail") == SMB_TOO_SHORT, f"7 characters answered {refused}")
        c.post("/auth/change-password", {"current_password": short, "new_password": tabbed}, api=mine)
        refused = c.post("/auth/me/smb/enable", {"current_password": tabbed}, expect=400, api=mine)
        c.expect(refused.get("detail") == SMB_CONTROL, f"a tab answered {refused}")
        refused = c.post("/auth/change-password", {"current_password": tabbed, "new_password": too_long}, expect=422,
                         api=mine)
        c.expect(f"{UNOS_PASSWORD_BYTES} bytes" in json.dumps(refused.get("detail")),
                 f"{UNOS_PASSWORD_BYTES + 1} bytes answered {refused}")
        found = smb_state(c.get("/auth/me/smb", api=mine))
        c.expect(found.startswith("password_needed,"), f"it says {found}")
        c.expect(user not in c.device.passdb_entries()[0], "Samba has an account for it")
        s.text += f": {SMB_TOO_SHORT}; {SMB_CONTROL}"

    with c.step("with a password that SMB can use, the user turns SMB on on the Account page's API without a "
                "two-factor code, and gets a marked host account and a Samba account"):
        c.post("/auth/change-password", {"current_password": tabbed, "new_password": password}, api=mine)
        on = c.post("/auth/me/smb/enable", {"current_password": password}, api=mine)
        c.expect(on["account"]["state"] == "active", f"it says {smb_state(on)}: {on.get('warnings')}")
        marked_account(c, user)

    with c.step("with write access to the share, the user's signed session lists it and writes a file, which gets the "
                "group unos-smb"):
        version = c.get(f"/shares/smb/{share}")["version"]
        c.call("PATCH", f"/shares/smb/{share}",
               {"expected_version": version, "access": [{"user_id": uid, "username": user, "level": "write"}]})
        listing = c.device.smb_list(user, password)
        c.expect(listing.ok and share in listing.disks, f"smbclient -L: {listing.status}, {listing.disks}")
        with c.device.smb_session(user, password, share) as smb:
            done = smb.run("ls", "put data.txt self.txt")
        c.expect(done.ok, f"smbclient: {done.status} {tail(done.output, 200)}")
        written = c.device.stat([f"{folder}/self.txt"])[f"{folder}/self.txt"]
        c.expect(written.endswith(":" + SMB_GROUP), f"self.txt is {written}")
        mine_shares = [(x["name"], x["level"], x["usable"]) for x in c.get("/auth/me/smb", api=mine)["shares"]]
        c.expect((share, "write", True) in mine_shares, f"the Account page's API lists {mine_shares}")

    with c.step("the user turns SMB off again on the same API: its Samba account is gone, SMB stays allowed, and its "
                "password is refused over SMB") as s:
        off = c.post("/auth/me/smb/disable", api=mine)
        c.expect(smb_state(off) == "password_needed, allowed True, two-factor code False", f"it says {smb_state(off)}")
        c.expect(user not in c.device.passdb_entries()[0], "pdbedit -L still lists it")
        if guests:  # map to guest = Bad User: Samba takes a user without an account as a guest (ADR-0339)
            s.text += "; a sign-in is not tried, as the device has guest shares"
        else:
            listing = c.device.smb_list(user, password)
            c.expect(not listing.ok and listing.status == "NT_STATUS_LOGON_FAILURE", f"it answered {listing.status}")
            s.text += f": {listing.status}"

    # shares.read-only-guest-share (ADR-0096, ADR-0339, ADR-0100).
    with c.step("guest access turned on for the share: the answer warns, the Shares page names it a guest share, and "
                "[global] has map to guest = Bad User, restrict anonymous = 1 and server signing = default"):
        c.upload(folder, "guest.txt", DATA)
        version = c.get(f"/shares/smb/{share}")["version"]
        changed = c.call("PATCH", f"/shares/smb/{share}", {"expected_version": version, "guest_ok": True})
        warnings = changed.get("warnings") or []
        c.expect(changed.get("guest_ok") is True and [w for w in warnings if w.startswith(GUEST_WARNS)],
                 f"guest access {changed.get('guest_ok')}, warnings {warnings}")
        named = c.get("/shares/smb-status").get("guest_shares") or []
        c.expect(share in named, f"the guest shares are {named}")
        conf = smb_global(c.device.read(SMB_CONF) or "")
        for key, (_, want) in GUEST_GLOBALS.items():
            c.expect(conf.get(key) == want, f"{key} = {conf.get(key)}, not {want}")
        c.expect(c.device.facts()["testparm"], "testparm refuses smb.conf")

    # A null session's share list (smbclient -L) is only reported: restrict anonymous = 1 lets
    # Samba's srvsvc refuse anonymous callers, so it names no share (ADR-0339).
    with c.step("an anonymous (null) session to the share lists it and reads its file, and a write is refused") as s:
        with c.device.smb_guest_session(share) as smb:
            done = smb.run("ls")
            c.expect(done.ok and "guest.txt" in done.output, f"ls: {done.status} {tail(done.output, 200)}")
            done = smb.run("get guest.txt got.txt", "!cat got.txt")
            c.expect(done.ok and DATA.strip() in done.output, f"smbclient: {done.status} {tail(done.output, 200)}")
            done = smb.run("put data.txt anonymous.txt")
        c.expect(done.status.startswith("NT_STATUS_"), f"the write answered {done.status}: {tail(done.output, 200)}")
        stat_is(c, c.device.stat([f"{folder}/anonymous.txt"]), f"{folder}/anonymous.txt", "missing")
        listing = c.device.smb_anonymous()
        s.text += (f": {done.status}; a null session's share list (smbclient -L) ends with {listing.status} and names "
                   + (", ".join(listing.disks) or f"no share ({tail(listing.output, 160) or 'no output'})"))

    with c.step("Delete removes the guest share's section from smb.conf and keeps its folder with its files"):
        c.call("DELETE", f"/shares/smb/{share}")
        c.expect(f"[{share}]" not in (c.device.read(SMB_CONF) or ""), "smb.conf still has the section")
        c.expect(c.device.read(f"{folder}/guest.txt") == DATA, "guest.txt is gone")

    signing = ("with the last guest share gone, server signing is mandatory again, with map to guest = Never and "
               "restrict anonymous = 2")
    if guests:
        c.skip(signing, f"the device has other guest shares: {', '.join(guests)}")
    else:
        with c.step(signing):
            named = c.get("/shares/smb-status").get("guest_shares") or []
            c.expect(not named, f"the guest shares are {named}")
            conf = smb_global(c.device.read(SMB_CONF) or "")
            for key, (want, _) in GUEST_GLOBALS.items():
                c.expect(conf.get(key) == want, f"{key} = {conf.get(key)}, not {want}")

    with c.step("no password of the check is in an answer, the supervisor's log, the journal, Samba's log or the audit log"):
        c.scanned(since, ("the supervisor's log", "the journal", "Samba's log", "the audit log"))

    with c.step("deleting the user removes its host account"):
        c.delete_user(uid, user)
        c.expect(not [line for line in c.device.facts()["accounts"] if line.split(":")[0] == user],
                 "getent passwd still has it")
        c.expect(user not in c.device.passdb_entries()[0], "pdbedit -L lists it")


# --- scrub -----------------------------------------------------------------------------------

# How long the scrub steps wait for btrfs, and how often they ask (the tests make both short).
SCRUB_WAIT = 900
SCRUB_POLL = 2
# A pool with more data than this is scrubbed only until btrfs reports the scrub running, and the
# scrub is then cancelled: the steps that let a scrub run to its end need a small pool.
SCRUB_TO_THE_END = 32 * GIB


def scrub_of(c: Check, pool: str) -> dict:
    return c.get(f"/storage/pools/{pool}/scrub")


def scrub_until_it_ends(c: Check, pool: str, progress: list[int] | None = None) -> dict:
    """The pool's scrub once btrfs no longer reports it running; PROGRESS gets the bytes scrubbed
    of each answer that said running."""
    deadline = time.monotonic() + SCRUB_WAIT
    while True:
        status = scrub_of(c, pool)
        if status.get("status") != "running":
            return status
        if progress is not None:
            progress.append(status.get("bytes_scrubbed") or 0)
        c.expect(time.monotonic() < deadline, f"the scrub of {pool} still runs after {SCRUB_WAIT} s")
        time.sleep(SCRUB_POLL)


def run_until_it_ends(c: Check, task_id: str) -> dict:
    """The last run of a Scheduler task once it has ended."""
    deadline = time.monotonic() + SCRUB_WAIT
    while True:
        runs = c.get(f"/scheduler/runs/{task_id}")
        if runs and runs[-1].get("finished_at"):
            return runs[-1]
        c.expect(time.monotonic() < deadline, f"the task's run did not end within {SCRUB_WAIT} s")
        time.sleep(SCRUB_POLL)


def scrub_recorded(c: Check, pool: str, before: str | None) -> dict:
    """The pool's scrub once btrfs has recorded the one started after ``before`` (the start time
    of the scrub recorded before it): btrfs records a scrub only about 5 seconds after its start
    (ADR-0313), and a cancel before that leaves no record to name the cancel."""
    deadline = time.monotonic() + SCRUB_WAIT
    while True:
        status = scrub_of(c, pool)
        if status.get("last_run") and status.get("last_run") != before:
            return status
        c.expect(time.monotonic() < deadline, f"btrfs did not record the scrub of {pool} within {SCRUB_WAIT} s")
        time.sleep(SCRUB_POLL)


def scrub_check(c: Check) -> None:
    with c.step("a pool is mounted read-write and managed by UNOS, and none of its scrubs runs") as s:
        pool = c.pick_pool()
        name = pool["name"]
        base = os.path.dirname(pool["mountpoint"])
        used = pool.get("used_size") or 0
        to_the_end = used <= SCRUB_TO_THE_END
        found = scrub_of(c, name)
        c.expect(found.get("status") != "running", f"a scrub of {name} runs: wait for its end, or cancel it")
        last = f"{found.get('status')} {found.get('last_run')}" if found.get("last_run") else found.get("status")
        s.text += f": {name} at {pool['mountpoint']}, {used / GIB:.1f} GiB used, its last scrub {last}"
    folder = f"{base}/{c.run}"
    task = c.run

    def own_tasks() -> list[dict]:
        return [t for t in c.get("/scheduler/tasks") if t.get("name") == task]

    def delete_own_tasks() -> None:
        for t in own_tasks():
            c.call("DELETE", f"/scheduler/tasks/{t['id']}")

    def cancel() -> None:
        """Cancel a scrub of the pool that still runs. Always: DELETE answers the status also when
        none runs, and a scrub that btrfs has not recorded yet may read as idle."""
        c.call("DELETE", f"/storage/pools/{name}/scrub")
        scrub_until_it_ends(c, name)

    with c.step("the device holds nothing of an earlier check"):
        c.baseline(lambda: {"the Scheduler's tasks": sorted(t.get("name", "") for t in c.get("/scheduler/tasks")),
                            f"the folders of {base}": c.device.pool_folders()})

    with c.step(f"a scrub of {folder}, a folder with nothing mounted on it, is refused with 409, and a "
                "scheduled scrub of it fails with the same text: one start for both (ADR-0311)") as s:
        c.on_cleanup(f"remove {folder}", lambda: c.device.pool_folders("remove", c.run))
        c.on_cleanup(f"delete the Scheduler's tasks {task}", delete_own_tasks)
        c.expect(c.run in c.device.pool_folders("make", c.run), f"{folder} was not made")
        refused = c.call("POST", f"/storage/pools/{c.run}/scrub", expect=409)
        want = f"Pool {c.run} is not mounted at {folder}"
        c.expect(refused.get("detail") == want, f"the answer is {refused.get('detail')!r}, not {want!r}")
        elsewhere = c.post("/scheduler/tasks", {"name": task, "task_type": "scrub", "enabled": False,
                                                "frequency": "daily", "hour": 3, "minute": 0, "target_pool": c.run})
        c.post(f"/scheduler/tasks/{elsewhere['id']}/run")
        failed = run_until_it_ends(c, elsewhere["id"])
        c.expect((failed.get("status"), failed.get("error")) == ("failed", want),
                 f"its run ended {failed.get('status')}: {failed.get('error') or failed.get('output')}")
        c.call("DELETE", f"/scheduler/tasks/{elsewhere['id']}")
        c.expect(c.run not in c.device.pool_folders("remove", c.run), f"{folder} is still there")
        s.text += f": {want!r}"

    with c.step(f"Start Scrub of {name} answers running, and so does the status right after it (ADR-0313)") as s:
        c.on_cleanup(f"cancel a scrub of {name} that still runs", cancel)
        first_before = scrub_of(c, name).get("last_run")
        started = c.post(f"/storage/pools/{name}/scrub")
        right_after = scrub_of(c, name)
        c.expect(started.get("status") == "running", f"Start Scrub answered {started.get('status')}")
        c.expect(right_after.get("status") == "running", f"the status right after the start is {right_after.get('status')}")
        s.text += f": {started.get('status')}, then {right_after.get('status')}"

    if to_the_end:
        with c.step(f"btrfs reports the scrub of {name}, its progress moving, until it ends, finished without "
                    "errors") as s:
            progress: list[int] = []
            ended = scrub_until_it_ends(c, name, progress)
            moved = [b for b in progress if b]
            c.expect(ended.get("status") == "finished", f"it ended {ended.get('status')}")
            c.expect(ended.get("errors") == 0, f"it found {ended.get('errors')} errors")
            c.expect(ended.get("last_run"), "it names no start time")
            c.expect(len(set(moved)) >= 2 and moved == sorted(moved), f"the bytes scrubbed while it ran: {progress}")
            s.text += (f": {ended.get('duration')} s, started {ended.get('last_run')}, "
                       f"{moved[0] / GIB:.2f} to {moved[-1] / GIB:.2f} GiB scrubbed while it ran")
    else:
        c.skip(f"btrfs reports the scrub of {name} until it ends", f"{used / GIB:.0f} GiB used: it is cancelled instead")

    with c.step(f"Cancel Scrub stops a scrub of {name} that btrfs has recorded, and the status says it was "
                "cancelled") as s:
        if to_the_end:
            before = scrub_of(c, name).get("last_run")
            started = c.post(f"/storage/pools/{name}/scrub")
            c.expect(started.get("status") == "running", f"Start Scrub answered {started.get('status')}")
        else:
            before = first_before  # the scrub of the start's step still runs
        recorded = scrub_recorded(c, name, before)
        c.expect(recorded.get("status") == "running", f"the scrub ended {recorded.get('status')} before the cancel")
        answer = c.call("DELETE", f"/storage/pools/{name}/scrub")
        ended = scrub_until_it_ends(c, name)
        c.expect(ended.get("status") == "aborted", f"the cancel answered {answer.get('status')}, and the scrub "
                                                   f"ended {ended.get('status')}")
        s.text += f": {answer.get('status')}, then {ended.get('status')}"

    with c.step(f"Run now of a scheduled scrub task of {name} starts a scrub") as s:
        scheduled_before = scrub_of(c, name).get("last_run")
        made = c.post("/scheduler/tasks", {"name": task, "task_type": "scrub", "enabled": False, "frequency": "daily",
                                           "hour": 3, "minute": 0, "target_pool": name})
        c.post(f"/scheduler/tasks/{made['id']}/run")
        run = run_until_it_ends(c, made["id"])
        c.expect((run.get("status"), run.get("output")) == ("success", "Scrub started"),
                 f"the run ended {run.get('status')}: {run.get('output') or run.get('error')}")
        status = scrub_of(c, name)
        c.expect(status.get("status") in ("running", "finished"), f"then the scrub is {status.get('status')}")
        s.text += f": {run.get('output')}, then {status.get('status')}"

    if to_the_end:
        with c.step(f"the scheduled scrub of {name} ends, finished without errors") as s:
            ended = scrub_until_it_ends(c, name)
            c.expect((ended.get("status"), ended.get("errors")) == ("finished", 0),
                     f"it ended {ended.get('status')} with {ended.get('errors')} errors")
            s.text += f": {ended.get('duration')} s"
    else:
        with c.step(f"Cancel Scrub stops the scheduled scrub of {name} once btrfs has recorded it"):
            recorded = scrub_recorded(c, name, scheduled_before)
            c.expect(recorded.get("status") == "running", f"the scrub ended {recorded.get('status')} before the cancel")
            c.call("DELETE", f"/storage/pools/{name}/scrub")
            c.expect(scrub_until_it_ends(c, name).get("status") == "aborted", "it did not end as cancelled")

    with c.step(f"the task {task} is deleted, and its run stays in the Scheduler's history"):
        c.call("DELETE", f"/scheduler/tasks/{made['id']}")
        c.expect(not own_tasks(), f"the task {task} is still there")
        c.expect([r["id"] for r in c.get(f"/scheduler/runs/{made['id']}")] == [run["id"]], "its run is gone")


# --- the off-device copy ------------------------------------------------------------------------

# Seconds a copy or a restore of the check's small folder may take (the repository on the pool's
# disk), and between two looks at the state.
OFFSITE_WAIT = 1800
OFFSITE_POLL = 2


def offsite_idle(c: Check) -> dict:
    """The state of the off-device copy once no copy or restore runs."""
    deadline = time.monotonic() + OFFSITE_WAIT
    while True:
        status = c.get("/offsite/status")
        if status.get("running") is None:
            return status
        c.expect(time.monotonic() < deadline, f"the {status['running'].get('kind')} still runs after {OFFSITE_WAIT} s")
        time.sleep(OFFSITE_POLL)


def utc_stamp(text: str) -> str:
    """The UTC time of restic's ISO 8601 TEXT as backups are named, YYYYmmdd_HHMMSS. restic writes
    nanoseconds, and Z for UTC, which datetime of Python 3.9 reads neither: the fraction is left out,
    and Z is written as +00:00."""
    plain = re.sub(r"Z$", "+00:00", re.sub(r"\.\d+", "", text))
    return datetime.fromisoformat(plain).astimezone(timezone.utc).strftime("%Y%m%d_%H%M%S")


def offsite_check(c: Check) -> None:
    """The off-device copy to a folder of a pool (ADR-0337): set up, Run now, the snapshots, both
    restores and Forget this target. It starts only on a device where no copy is set up and no task of
    the Scheduler copies: the check sets one up and forgets it again, and such a task would copy to its
    target meanwhile and fail every run after it.

    The API has no route that cancels a copy or a restore: one that outlives the waits (OFFSITE_WAIT)
    still runs at the clean-up, whose steps then fail, and the final comparison reports what is left
    on the device.
    """
    with c.step("a pool is mounted read-write and managed by UNOS") as s:
        pool = c.pick_pool()
        name, root = pool["name"], pool["mountpoint"]
        s.text += f": {name} at {root}"
    source = f"{root}/{c.run}"
    target = f"{root}/{c.run}t"

    def listed(path: str) -> list[str]:
        return [i["name"] for i in c.get("/files/list", params={"path": path})["items"]]

    def backups() -> list[str]:
        return sorted(b["filename"] for b in c.get("/backup/list")["backups"])

    def state() -> dict:
        return {
            "the off-device copy": "set up" if c.get("/offsite/settings")["configured"] else "not set up",
            "the pool's top folder": sorted(listed(root)),
            "the backups": backups(),
        }

    def remove_settings() -> None:
        offsite_idle(c)
        c.call("DELETE", "/offsite/settings")

    def remove_restored() -> None:
        offsite_idle(c)
        for folder in listed(root):
            if folder.startswith("restored-") and folder not in c.before["the pool's top folder"]:
                c.delete_for_good(f"{root}/{folder}")

    def remove_backups() -> None:
        for backup in backups():
            if backup.startswith("offsite_") and backup not in c.before["the backups"]:
                c.call("DELETE", f"/backup/{backup}", expect=(200, 404))

    with c.step("the device holds nothing of an earlier check"):
        c.baseline(state)
        since = c.device.now()

    with c.step("no off-device copy is set up, which the check would replace"):
        c.expect(c.before["the off-device copy"] == "not set up", "one is set up on System > Backup & Restore: forget "
                 "it there, or run the check on a device without one")
        copying = [task.get("name") for task in c.get("/scheduler/tasks") if task.get("task_type") == "offsite_copy"]
        c.expect(not copying, f"the Scheduler has a task that copies: {', '.join(copying)}. It would copy to the "
                 "check's target meanwhile and fail every run after the check: delete it, or run the check on a "
                 "device without one")

    with c.step(f"a folder {source} with two files, the folder to copy"):
        c.on_cleanup(f"delete {source} for good", lambda: c.delete_for_good(source))
        c.post("/files/mkdir", {"path": root, "name": c.run})
        c.upload(source, "a.txt", DATA)
        c.post("/files/mkdir", {"path": source, "name": "sub"})
        c.upload(f"{source}/sub", "b.txt", DATA2)

    password = c.secret()
    with c.step(f"Save and test makes the copy repository in {target} (700 root:root), and the answer says that it "
                "lies on the disk it copies"):
        c.on_cleanup(f"delete {target} for good", lambda: c.delete_for_good(target))
        c.on_cleanup("forget the target: the settings, the password and the state", remove_settings)
        answer = c.call("PUT", "/offsite/settings", {"kind": "disk", "path": target, "password": password,
                                                     "sources": [source]})
        c.expect(answer.get("configured") and answer.get("has_password"), f"the answer is {answer}")
        warnings = [w.get("kind") for w in answer.get("warnings") or []]
        c.expect(warnings == ["same_disk"], f"its warnings are {warnings}")
        found = listed(target)
        c.expect("config" in found and "keys" in found, f"{target} holds {found}")
        stat_is(c, c.device.stat([target]), target, "700 root:root")

    with c.step("Run now copies the configuration and the folder, removes old snapshots, checks the repository and "
                "tests a restore") as s:
        c.post("/offsite/run", expect=202)
        last = offsite_idle(c).get("last_copy") or {}
        c.expect(last.get("success"), f"the copy failed at {last.get('step')}: {last.get('error')}")
        c.expect((last.get("check"), last.get("restore_test")) == ("passed", "passed"),
                 f"the check {last.get('check')}, the restore test {last.get('restore_test')}")
        summary = last.get("summary") or {}
        c.expect(summary.get("files_new") == 2, f"it copied {summary}")
        s.text += f": {summary.get('files_new')} new files, {summary.get('bytes_added')} bytes added"

    with c.step("the snapshots are one of the configuration and one of the folder"):
        snapshots = c.get("/offsite/snapshots")["snapshots"]
        kinds = {snapshot["kind"]: snapshot for snapshot in snapshots}
        c.expect(len(snapshots) == 2 and sorted(kinds) == ["configuration", "data"],
                 f"it lists {[(x.get('kind'), x.get('paths')) for x in snapshots]}")
        c.expect(kinds["data"]["paths"] == [source], f"the folder's snapshot holds {kinds['data']['paths']}")

    stamp = utc_stamp(kinds["configuration"]["time"])
    with c.step(f"Restore configuration puts it into the backup list as offsite_{stamp}.tar.gz, and Delete "
                "removes it there") as s:
        c.on_cleanup("delete the backups of the check's restores", remove_backups)
        backup = c.post("/offsite/restore-configuration", {"snapshot_id": kinds["configuration"]["id"]})["backup"]
        name_of = backup["filename"]
        c.expect(re.fullmatch(rf"offsite_{stamp}(_\d+)?\.tar\.gz", name_of) and name_of in backups(),
                 f"{name_of} is not listed")
        c.call("DELETE", f"/backup/{name_of}")
        c.expect(name_of not in backups(), f"{name_of} is still listed")
        s.text += f": {name_of}"

    with c.step(f"Restore files restores the folder into a new folder restored-<time> of {name}, byte for byte") as s:
        c.on_cleanup("delete the folders of the check's restores for good", remove_restored)
        folder = c.post("/offsite/restore-files", {"snapshot_id": kinds["data"]["id"], "pool": name, "path": source},
                        expect=202)["folder"]
        c.expect(re.fullmatch(rf"{re.escape(root)}/restored-\d{{8}}-\d{{6}}", folder), f"it restores into {folder}")
        last = offsite_idle(c).get("last_restore") or {}
        c.expect(last.get("success") and last.get("folder") == folder, f"the restore ended {last}")
        c.expect(c.download(f"{folder}/a.txt") == DATA and c.download(f"{folder}/sub/b.txt") == DATA2,
                 "the restored files hold other bytes")
        s.text += f": {folder}"

    with c.step("no password of the check is in an answer, the supervisor's log, the journal or the audit log"):
        c.scanned(since, ("the supervisor's log", "the journal", "the audit log"))

    with c.step("Forget this target removes the settings, the password and the state: no copy is set up"):
        c.call("DELETE", "/offsite/settings")
        c.expect(not c.get("/offsite/settings")["configured"], "a copy is still set up")


# --- restore ---------------------------------------------------------------------------------

# The output of a scheduled backup's run that names its file (src/scheduler/router.py), and the
# warning of a restore for a restored SMB account that needs its password again
# (src/shares/accounts.py); test_dev_scenarios.py compares both with the code.
SCHEDULED_BACKUP = re.compile(r"Backup (auto_backup_[0-9_]+\.tar\.gz\.enc) created\.")
TURN_ON_AGAIN = "SMB must be turned on again for "
# How many copies of the database from before a restore src/backup/router.py keeps: the newest.
PRE_RESTORE_COPIES = 5
# The check's restores: each keeps a copy, which the clean-up removes again unless a restore changed
# what is not the check's (restore_check).
RESTORES = 2


def names_in(value: str | None) -> list[str]:
    """The names of an smb.conf list (valid users, write list), sorted."""
    return sorted(n.strip() for n in (value or "").split(",") if n.strip())


def restore_check(c: Check) -> None:
    """Restores of a backup that the check makes by hand moments before (ADR-0341):
    shares.restore-keeps-smb-with-proof and the device part of backup.no-old-backup-import.

    A restore puts the whole database back as it was at the backup, so the check restores only its
    own backup, made after its baseline, and compares the users, the shares, the API tokens of the
    relay's administrator (GET /auth/tokens lists only the caller's), smb.conf, the SMB host
    accounts and Samba's accounts with it afterwards. What it makes after
    the backup and a restore drops from the database is a user with SMB on: the post-restore
    reconcile removes its host account and Samba account (stale, ADR-0110), and when a restore
    left them, the clean-up restarts the supervisor, whose startup reconcile does. Each restore keeps
    a copy of the database from before it, of which UNOS keeps the newest PRE_RESTORE_COPIES: the
    check starts only when its restores push out no copy that was there before, and removes the
    copies that are new since its baseline, which only restores make, once no restore can still run;
    they stay when what is not the check's changed around a restore or since the baseline, as they
    hold it from before the restore. A restored row with the uid of a live account of another name comes only from a backup of another
    installation or a crafted archive (ADR-0112): uids are handed out once on one installation, also
    across restores (ADR-0109), so the check shows the stale account's uid instead, and the backend
    tests show that case (test_restore_accounts.py).
    """
    with c.step("a pool is mounted read-write and managed by UNOS") as s:
        pool = c.pick_pool()
        root = pool["mountpoint"]
        s.text += f": {pool['name']} at {root}"
    share = task = c.run
    folder = f"{root}/{c.run}"
    # same keeps its password of the backup, changed gets a new one after it; dropped is made after
    # the backup, fresh after the second restore.
    same, changed, dropped, fresh = (c.run + suffix for suffix in "scdf")
    passwords = {name: c.secret() for name in (same, changed, dropped, fresh)}
    newer, again, passphrase = c.secret(), c.secret(), c.secret()
    ids: dict[str, int] = {}
    uids: dict[str, int] = {}
    made: list[str] = []  # the names the API gave the check's backups
    task_ids: list[str] = []  # the ids of the check's backup tasks
    kept: list[str] = []  # what the restores changed beyond the check's own: the copies hold it from before
    seen: list[dict] = []  # others() right before each restore, also one whose answer is lost

    def backups() -> list[str]:
        return sorted(b["filename"] for b in c.get("/backup/list")["backups"])

    def host_accounts() -> dict[str, int]:
        fields = [line.split(":") for line in c.device.facts()["accounts"]]
        return {f[0]: int(f[2]) for f in fields if len(f) > 2 and f[2].isdigit()}

    def view() -> dict:
        """What a restore of a backup made moments before must keep (and the end compares)."""
        facts = c.device.facts()
        return {
            "the users": sorted(f"{u['username']} " + ("admin" if u["is_admin"] else
                                                       f"{(u.get('smb') or {}).get('state')} {(u.get('smb') or {}).get('uid')}")
                                for u in c.get("/auth/users")),
            "the shares": sorted(f"{s['name']} {s['path']} {s['comment']!r} guest {s['guest_ok']} "
                                 + ",".join(f"{a['username']}:{a['level']}" for a in s["access"])
                                 for s in c.get("/shares/smb")),
            "the API tokens": sorted(f"{t['id']} {t['name']} {t['token_prefix']} {t['is_active']} {t['permissions']} "
                                     f"{t.get('expires_at')}" for t in c.get("/auth/tokens")),
            "smb.conf": facts["sha256"].get(SMB_CONF),
            "the SMB host accounts": facts["accounts"],
            "Samba's accounts": c.device.passdb_entries()[0],
        }

    def others(found: dict) -> dict:
        """What FOUND (a view() or the baseline) holds that is not the check's: the users, shares, API
        tokens of the relay's administrator and accounts whose entries do not name the run. A restore
        that changes them loses what only the copy of the database from before it still holds."""
        return {key: [x for x in found[key] if c.run not in x]
                for key in ("the users", "the shares", "the API tokens", "the SMB host accounts", "Samba's accounts")}

    def state() -> dict:
        facts = c.device.facts()
        return {
            **view(),
            "the account files": {f: facts["sha256"].get(f) for f in ACCOUNT_FILES},
            "the backups": backups(),
            "the copies of the database from before a restore": c.device.db_copies(),
            "the Scheduler's tasks": sorted(t.get("name", "") for t in c.get("/scheduler/tasks")),
            "the pool's top folder": sorted(i["name"] for i in c.get("/files/list", params={"path": root})["items"]),
        }

    def own_tasks() -> list[dict]:
        return [t for t in c.get("/scheduler/tasks") if t.get("name") == task]

    def delete_own_tasks() -> None:
        for t in own_tasks():
            if t["id"] not in task_ids:  # one whose create answer was lost
                task_ids.append(t["id"])
            c.call("DELETE", f"/scheduler/tasks/{t['id']}", expect=(200, 404))

    def task_backups() -> list[str]:
        """The backups that the runs of the check's tasks name, once each run has ended: also one
        whose Run now answer was lost. The runs stay in the Scheduler's history after the task."""
        found = []
        for task_id in task_ids:
            if c.get(f"/scheduler/runs/{task_id}"):
                run_until_it_ends(c, task_id)
            for run in c.get(f"/scheduler/runs/{task_id}"):
                found += SCHEDULED_BACKUP.findall(run.get("output") or "")
        return found

    def remove_backups() -> None:
        """Delete the check's backups: those the API named, and those the runs of its tasks name. One
        made by hand whose answer was lost has no name the check knows: it is named, never deleted,
        since a backup made by hand meanwhile would look the same."""
        for name in made:  # first: a failed look at the runs below leaves none of these
            c.call("DELETE", f"/backup/{name}", expect=(200, 404))
        for name in task_backups():
            c.call("DELETE", f"/backup/{name}", expect=(200, 404))
        assert c.before is not None
        unknown = [n for n in backups() if n not in c.before["the backups"] and n not in made]
        c.expect(not unknown, "new backups whose names the check never got, " + ", ".join(unknown)
                 + ": delete each one that is the check's (scripts/dev/api.py DELETE /backup/<name>)")

    def remove_copies() -> None:
        """Remove the copies of the database that are new since the baseline: those of the check's
        restores, also of one whose answer was lost, once it has ended. They stay while a restore
        may have changed what is not the check's, which they hold from before it."""
        assert c.before is not None
        wait_for_smb_lock(c)
        found = c.before["the copies of the database from before a restore"]
        new = tuple(n for n in c.device.db_copies() if n not in found)
        if not new:
            return
        now = others(view())
        changed = sorted({key for then in [others(c.before), *seen] for key in now if now[key] != then[key]})
        why = kept or ([f"{', '.join(changed)} that are not the check's differ from how they were at the baseline or "
                        "before a restore"] if changed else [])
        c.expect(not why, f"{', '.join(new)} stay, as they hold the database from before a restore and "
                          f"{'; '.join(why)}: removing them is the owner's decision")
        left = [n for n in c.device.db_copies("remove", new) if n in new]
        c.expect(not left, f"{', '.join(left)} are still there")

    def dropped_left() -> bool:
        return dropped in host_accounts() or dropped in c.device.passdb_entries()[0]

    def remove_dropped() -> None:
        wait_for_smb_lock(c)  # a restore whose answer was lost, with its reconcile
        c.delete_user_named(dropped)
        if dropped_left():  # a restore dropped its row and left them: the startup reconcile removes them
            restart_and_wait(c)
            c.expect(not dropped_left(), f"{dropped} still has a host account or a Samba account after a restart of "
                                         "the supervisor")

    def make_user(name: str) -> None:
        answer = c.post("/auth/users", {"username": name, "password": passwords[name], "smb_enabled": True})
        smb = answer.get("smb") or {}
        c.expect(smb.get("state") == "active" and isinstance(smb.get("uid"), int),
                 f"the SMB of {name} is {smb.get('state')}, uid {smb.get('uid')}: {answer.get('warnings')}")
        ids[name], uids[name] = answer["id"], smb["uid"]

    def entry(name: str) -> dict:
        return {"user_id": ids[name], "username": name, "level": "write"}

    def principals(conf: str | None = None) -> tuple[list[str], list[str]]:
        """The valid users and the write list of the share's section in smb.conf (CONF, or as read now)."""
        if conf is None:
            conf = c.device.read(SMB_CONF) or ""
        section = smb_section(conf, share) or {}
        return names_in(section.get("valid users")), names_in(section.get("write list"))

    def holds_no_smb_conf(name: str, members: list[str]) -> None:
        c.expect("manifest.json" in members and "unos.db" in members, f"{name} holds {members}")
        conf = [m for m in members if "smb.conf" in m]
        c.expect(not conf, f"{name} holds {', '.join(conf)}")

    def restored(answer: dict) -> list[str]:
        items = answer.get("restored") or []
        c.expect(answer.get("status") == "success" and "database" in items and "smb_config" in items,
                 f"it answered {answer.get('status')}, restored {items}: {answer.get('message')}")
        return items

    with c.step("the device holds nothing of an earlier check"):
        c.baseline(state)

    with c.step("no share that is not served has access, which a restore would remove (ADR-0145), Samba runs UNOS's "
                "smb.conf without problems, and the device has smbclient, stdbuf, testparm and pdbedit"):
        unserved = [s["name"] for s in c.get("/shares/smb") if not s.get("served") and (s.get("access") or s.get("guest_ok"))]
        c.expect(not unserved, f"shares that are not served have access, which a restore removes: {', '.join(unserved)}")
        status = c.get("/shares/smb-status")
        c.expect(not status.get("problems"), "the Shares page reports: " + "; ".join(status.get("problems") or []))
        facts = c.device.facts()
        missing = sorted({"smbclient", "stdbuf", "testparm", "pdbedit", "getent"} - set(facts["tools"]))
        c.expect(not missing, f"the device lacks {', '.join(missing)} (installing one is an owner action)")
        c.expect(facts["testparm"], "testparm refuses smb.conf")
        since = facts["now"]
        guests = sorted(status.get("guest_shares") or [])

    with c.step(f"the check's {RESTORES} restores push out none of the copies of the database from before a restore, "
                f"of which UNOS keeps {PRE_RESTORE_COPIES}") as s:
        assert c.before is not None
        copies = c.before["the copies of the database from before a restore"]
        c.expect(len(copies) + RESTORES <= PRE_RESTORE_COPIES,
                 f"the device has {len(copies)}, {', '.join(copies)}, and the check's restores would delete the "
                 "oldest: removing those that are not needed is the owner's decision")
        s.text += f": the device has {len(copies)}"

    with c.step(f"two users {same} and {changed} with SMB on get marked host accounts with their uids and Samba "
                "accounts") as s:
        for name in (same, changed):
            c.on_cleanup(f"delete the user {name}", lambda name=name: c.delete_user_named(name))
            make_user(name)
        accounts = host_accounts()
        c.expect(all(accounts.get(n) == uids[n] for n in (same, changed)), f"getent passwd has {accounts}")
        found = c.device.passdb_entries()[0]
        c.expect(same in found and changed in found, "pdbedit -L lacks one")
        s.text += f": uids {uids[same]} and {uids[changed]}"

    with c.step(f"the share {share} on a new folder with write access for both, which Samba serves with both in "
                "its section"):
        c.on_cleanup(f"delete {folder} for good", lambda: c.delete_for_good(folder))
        c.on_cleanup(f"delete the share {share}", lambda: c.delete_share(share))
        answer = c.post("/shares/smb", {"name": share, "path": folder, "access": [entry(same), entry(changed)]})
        c.expect(answer.get("served") and answer.get("available"),
                 f"it is served {answer.get('served')}, available {answer.get('available')}: {answer.get('path_problem')}")
        valid, writers = principals()
        c.expect(valid == writers == sorted([same, changed]), f"its section has valid users {valid}, write list {writers}")

    with c.step("a backup made by hand holds no smb.conf") as s:
        c.on_cleanup("delete the check's backups", remove_backups)
        plain = c.post("/backup/create", {})["backup"]["filename"]
        made.append(plain)
        c.expect(plain in backups(), f"{plain} is not listed")
        members = c.device.archive_members(plain)
        holds_no_smb_conf(plain, members)
        s.text += f": {plain} holds {', '.join(members)}"

    with c.step(f"a backup made by Run now of a disabled scheduled backup task {task} holds no smb.conf, and it is "
                "deleted with its task") as s:
        c.on_cleanup(f"delete the Scheduler's task {task}", delete_own_tasks)
        answer = c.post("/scheduler/tasks", {"name": task, "task_type": "backup", "enabled": False, "frequency": "daily",
                                             "hour": 3, "minute": 0, "passphrase": passphrase})
        task_ids.append(answer["id"])
        c.expect(answer.get("has_backup_key") is True, f"the task has no key: {answer.get('has_backup_key')}")
        c.post(f"/scheduler/tasks/{answer['id']}/run")
        run = run_until_it_ends(c, answer["id"])
        named = SCHEDULED_BACKUP.match(run.get("output") or "")
        c.expect(run.get("status") == "success" and named,
                 f"its run ended {run.get('status')}: {run.get('output') or run.get('error')}")
        scheduled = named.group(1)  # type: ignore[union-attr]
        members = c.device.archive_members(scheduled, passphrase)
        holds_no_smb_conf(scheduled, members)
        c.call("DELETE", f"/backup/{scheduled}")
        c.call("DELETE", f"/scheduler/tasks/{answer['id']}")
        c.expect(scheduled not in backups() and not own_tasks(), "the backup or the task is still there")
        s.text += f": {scheduled} holds {', '.join(members)}"

    with c.step(f"a restore of {plain}, with nothing changed since, keeps the users, the shares, the API tokens, the "
                "SMB host accounts and Samba's accounts, and smb.conf byte for byte") as s:
        before = view()
        c.on_cleanup("remove the copies of the database that the check's restores made", remove_copies)
        seen.append(others(before))
        answer = c.post(f"/backup/restore/{plain}", {})
        after = view()
        differ = [key for key in before if after.get(key) != before[key]]
        theirs, now = others(before), others(after)
        if [key for key in theirs if now[key] != theirs[key]]:
            kept.append(f"the first restore changed {', '.join(key for key in theirs if now[key] != theirs[key])} that "
                        "are not the check's")
        items = restored(answer)
        asked = [w for w in answer.get("warnings") or [] if w.startswith(TURN_ON_AGAIN)]
        c.expect(not asked, f"it answered {asked}")
        c.expect(not differ, "; ".join(f"{key}: {describe(before[key], after.get(key))}" for key in differ))
        s.text += f": restored {', '.join(items)}"

    with c.step(f"after the backup, {changed} gets a new password, which signs in, and a new user {dropped} with SMB "
                "on gets write access to the share") as s:
        c.call("PUT", f"/auth/users/{ids[changed]}", {"password": newer, "username": changed})
        listing = c.device.smb_list(changed, newer)
        c.expect(listing.ok and share in listing.disks, f"smbclient -L: {listing.status}, {listing.disks}")
        c.on_cleanup(f"delete the user {dropped}, and its host account and Samba account if a restore left them",
                     remove_dropped)
        make_user(dropped)
        c.expect(uids[dropped] > max(uids[same], uids[changed]), f"it got uid {uids[dropped]}")
        version = c.get(f"/shares/smb/{share}")["version"]
        c.call("PATCH", f"/shares/smb/{share}", {"expected_version": version,
                                                 "access": [entry(same), entry(changed), entry(dropped)]})
        valid, _ = principals()
        c.expect(valid == sorted([same, changed, dropped]), f"the share's section has valid users {valid}")
        s.text += f": uid {uids[dropped]}"

    with c.step(f"the same restore again puts the users and the share back as at the backup: {same} keeps SMB on, "
                f"{changed} needs its password again, both with their uids, and {dropped} is gone, while what is not "
                "the check's stays") as s:
        theirs = others(view())
        seen.append(theirs)
        answer = c.post(f"/backup/restore/{plain}", {})
        now = others(view())
        differ = [key for key in theirs if now[key] != theirs[key]]
        if differ:
            kept.append(f"the second restore changed {', '.join(differ)} that are not the check's")
        c.expect(not differ, "what is not the check's changed: "
                 + "; ".join(f"{key}: {describe(theirs[key], now[key])}" for key in differ))
        restored(answer)
        asked = sorted(w for w in answer.get("warnings") or [] if w.startswith(TURN_ON_AGAIN))
        c.expect(asked == [TURN_ON_AGAIN + changed], f"it asks {asked}")
        users = {u["username"]: u.get("smb") or {} for u in c.get("/auth/users")}
        c.expect(dropped not in users, f"{dropped} is still a user")
        for name, want in ((same, "active"), (changed, "password_needed")):
            got = users.get(name, {})
            c.expect((got.get("state"), got.get("uid")) == (want, uids[name]),
                     f"the SMB of {name} is {got.get('state')} with uid {got.get('uid')}, not {want} with {uids[name]}")
        access = sorted((a["username"], a["level"]) for a in c.get(f"/shares/smb/{share}")["access"])
        c.expect(access == [(changed, "write"), (same, "write")], f"the share's access is {access}")
        s.text += f": {asked[0]}"

    with c.step(f"smb.conf is UNOS's rendering of the restored database: the share's section lets in {same} alone, "
                f"not {changed}, which needs its password, nor {dropped}, and testparm takes it"):
        conf = c.device.read(SMB_CONF) or ""
        c.expect(conf.startswith(SMB_HEADER), "smb.conf does not start with UNOS's header")
        valid, writers = principals(conf)
        c.expect(valid == writers == [same], f"the share's section has valid users {valid}, write list {writers}")
        c.expect(dropped not in conf, f"smb.conf names {dropped}")
        c.expect(c.device.facts()["testparm"], "testparm refuses smb.conf")

    with c.step(f"the host account and Samba account of {dropped}, which no restored row has, are removed; {same} "
                f"and {changed} keep their host accounts with their uids, and {same} alone its Samba account"):
        accounts = host_accounts()
        c.expect(dropped not in accounts, f"{dropped} still has its host account, uid {accounts.get(dropped)}")
        c.expect(all(accounts.get(n) == uids[n] for n in (same, changed)),
                 f"getent passwd has {', '.join(f'{n} {u}' for n, u in sorted(accounts.items()) if n.startswith(c.run))}")
        found = [n for n in c.device.passdb_entries()[0] if n.startswith(c.run)]
        c.expect(found == [same], f"pdbedit -L lists {found}")

    with c.step(f"{same} signs in with its password of the backup, in a signed session that writes to the share"):
        with c.device.smb_session(same, passwords[same], share) as smb:
            done = smb.run("put data.txt s.txt")
        c.expect(done.ok, f"smbclient: {done.status} {tail(done.output, 200)}")
        c.expect(c.device.read(f"{folder}/s.txt") == DATA, "s.txt holds other bytes")

    refused = (f"the password of {changed} at the backup and its newer one are both refused with "
               "NT_STATUS_LOGON_FAILURE")
    if guests:  # map to guest = Bad User: Samba takes a user without a Samba account as a guest (ADR-0339)
        c.skip(refused, f"the device has guest shares ({', '.join(guests)}), so Samba takes a user without a Samba "
                        f"account as a guest (ADR-0339); pdbedit -L lists none of {changed}")
    else:
        with c.step(refused):
            for which, password in (("the backup's", passwords[changed]), ("the newer", newer)):
                listing = c.device.smb_list(changed, password)
                c.expect(not listing.ok and listing.status == "NT_STATUS_LOGON_FAILURE",
                         f"{which} answered {listing.status}")

    with c.step(f"{changed} gets SMB on again with a password set by an administrator, under its uid "
                f"{uids[changed]}, and signs in with it"):
        answer = c.post(f"/auth/users/{ids[changed]}/smb/enable", {"username": changed, "password": again})
        smb_state = answer.get("smb") or {}
        c.expect((smb_state.get("state"), smb_state.get("uid")) == ("active", uids[changed]),
                 f"its SMB is {smb_state.get('state')} with uid {smb_state.get('uid')}: {answer.get('warnings')}")
        listing = c.device.smb_list(changed, again)
        c.expect(listing.ok and share in listing.disks, f"smbclient -L: {listing.status}, {listing.disks}")

    with c.step(f"a new user {fresh} with SMB on gets a uid above {uids[dropped]} of {dropped}, which the backup's "
                "next uid would have given again (ADR-0109)") as s:
        c.on_cleanup(f"delete the user {fresh}", lambda: c.delete_user_named(fresh))
        make_user(fresh)
        c.expect(uids[fresh] > uids[dropped], f"it got uid {uids[fresh]}")
        s.text += f": {uids[fresh]}"

    with c.step("no password of the check is in an answer, the supervisor's log, the journal, Samba's log or the "
                "audit log"):
        c.scanned(since, ("the supervisor's log", "the journal", "Samba's log", "the audit log"))

    with c.step("Delete removes the share's section from smb.conf, the users' Delete their host accounts and Samba "
                "accounts, and the backup's Delete the backup"):
        c.call("DELETE", f"/shares/smb/{share}")
        c.expect(f"[{share}]" not in (c.device.read(SMB_CONF) or ""), "smb.conf still has the section")
        for name in (fresh, changed, same):
            c.call("DELETE", f"/auth/users/{ids[name]}", params={"username": name})
        accounts, found = host_accounts(), c.device.passdb_entries()[0]
        left = [n for n in (same, changed, fresh) if n in accounts or n in found]
        c.expect(not left, f"{', '.join(left)} still have an account")
        c.call("DELETE", f"/backup/{plain}")
        c.expect(plain not in backups(), f"{plain} is still listed")


CHECKS: dict[str, Callable[[Check], None]] = {
    "files": files_check,
    "volumes": volumes_check,
    "smart": smart_check,
    "backups": backups_check,
    "smb": smb_check,
    "smbconf": smbconf_check,
    "shares": shares_check,
    "scrub": scrub_check,
    "offsite": offsite_check,
    "restore": restore_check,
}


# --- main ------------------------------------------------------------------------------------


def pool_name(value: str) -> str:
    if not POOL_NAME.fullmatch(value):
        raise argparse.ArgumentTypeError(f"not a pool name: {value!r}")
    return value


def interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt()


def lock_holder_alive(pid: str) -> bool:
    """Whether PID (UNOS_DEV_LOCK_HELD) is a running process, as dev_lock of env.sh checks."""
    if not pid.isdigit():
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scenario.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("area", choices=AREAS)
    parser.add_argument("--pool", type=pool_name,
                        help="the pool of the files, volumes, smb, smbconf, shares, scrub, offsite and restore checks")
    args = parser.parse_args(argv)
    if not lock_holder_alive(os.environ.get("UNOS_DEV_LOCK_HELD", "")):
        print("scenario.py: run it through scripts/dev/scenario.sh, which holds the device lock (ADR-0210)", file=sys.stderr)
        return 2
    try:
        api = relay.DeviceApi()
    except relay.ApiError as e:
        print(f"scenario.py: {e}", file=sys.stderr)
        return 2
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupt)
    check = Check(args.area, api, Device(settings=api.settings), pool=args.pool, signals=True)
    status = check.execute(CHECKS[args.area])
    if check.output_lost:  # flushing the lost output at exit would fail and exit 120
        os._exit(status)
    return status


if __name__ == "__main__":
    sys.exit(main())
