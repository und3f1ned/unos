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
           uses the volume, and the Delete once the share is gone.
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
           the expected version and a stale one refused, a password change that ends the open
           session, the replaced password refused without a write to passdb.tdb, the new one
           taken, a wrong password refused and counted in passdb.tdb, an anonymous session
           refused, 5 wrong passwords in a row that lock the account, so that the right one is
           refused too, without a write (ADR-0334), the [global] lines without a guest share and
           of Samba's log in /run/samba, no password of the check in an answer or a log, the
           share's Delete keeping its folder, and the user's Delete removing its accounts, still
           locked.
  smbconf  smb.conf edited by hand and what the Shares page says (ADR-0309): a share section
           added by hand (available = no) and UNOS's header removed, which the Shares page names
           before an apply; a new share whose apply copies that file byte for byte to
           smb.conf.unos-replaced (ADR-0092) and makes smb.conf UNOS's again, and the copy named
           on the Shares page; a comment saved with the expected version and a stale one refused;
           the share's folder replaced by a symbolic link, so that the share is not served, with
           access refused without a new folder and saved with one; Delete keeping the folder with
           its files and smb.conf byte for byte as found; then, with the copy deleted, a restart
           of the supervisor that writes no file in /etc/samba and leaves no problem.
  shares   The Shares and Account pages beyond slice 2: a sticky folder of root with a sticky
           folder "theirs" of another account in it, made over ssh on the pool, where a new share
           folder in "theirs" is refused with 409 and the reason, and a share of "theirs" itself
           is accepted with the warning that names its owner, reported on the Shares page and
           refused by Repair permissions (ADR-0193); a share on a new folder with entries of
           other groups and modes made over ssh, which Repair permissions gives the group
           unos-smb, group read and write and setgid on folders, keeping their owners, while a
           private file and folder, a symbolic link and the file outside the share that it
           points to stay as they were, and an ACL entry (where the device has setfacl) stays
           with the group bits as its mask (ADR-0155); the last run with its counts on the Shares
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

The API is called through api.py's relay (ADR-0222). The commands on the device run through
ssh.sh, in a private mount namespace with the root filesystem read-only (ADR-0221), with scratch
files in /dev/shm: stat, the account files' digests, testparm, Samba's log sizes, smbclient with
a client configuration of its own in RAM, the scan for passwords, the shares check's entries on
the pool (which the namespace leaves writable) and getfacl; only pdbedit runs without that
namespace, since tdbsam opens passdb.tdb read-write, and the smbconf check's edit of smb.conf by
hand, which writes /etc/samba (ADR-0309), and the scrub check's folder below the pools'
base, on the root filesystem (ADR-0314). The passwords of the temporary user and the
backup's passphrase are made here, go to the device only on standard input (to the relay, and
into an auth file of mode 600 in RAM that smbclient reads), and are never printed. The shares
check's user signs in through the relay with a token cache of its own in RAM (USER_TOKEN, mode
600 like the administrator's, ADR-0222), which its clean-up deletes. The checks start no
container (ADR-0183); the smbconf check restarts the supervisor's (ADR-0309).

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
AREAS = ("files", "volumes", "smart", "backups", "smb", "smbconf", "shares", "scrub", "offsite")
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
# The copy of an smb.conf with share sections that UNOS did not write (ADR-0092), in /etc/samba.
REPLACED_COPY = "smb.conf.unos-replaced"
# What GET /shares/smb-status says of an smb.conf that is not UNOS's (ADR-0249), as
# src/shares/service.py words it (test_dev_scenarios.py compares).
CONF_NOT_UNOS = "smb.conf was replaced or removed after UNOS wrote it"
# The fixed lines that end every share section UNOS renders (src/shares/smbconf.py; ADR-0147,
# ADR-0150): a share section without them is one that UNOS did not write (ADR-0092).
UNOS_SHARE_LINES = (("veto files", "/.snapshots/.unos-trash/"), ("force group", "unos-smb"), ("create mask", "0660"),
                    ("force create mode", "0660"), ("directory mask", "2770"), ("force directory mode", "2770"))
NOT_SHARES = ("global", "homes", "printers", "print$")
# The start of the guest warning in the answer of a change that turns guest access on
# (GUEST_WARNING of src/shares/service.py, ADR-0096; test_dev_scenarios.py compares).
GUEST_WARNS = "Anyone on your network can read this share without a password"
# The [global] lines that follow whether a guest share exists (ADR-0098, ADR-0100): without one,
# with one.
GUEST_GLOBALS = {"map to guest": ("Never", "Bad User"), "restrict anonymous": ("2", "1"),
                 "server signing": ("mandatory", "default")}
# Why SMB refuses a password (smb_password_problem of src/shares/constants.py, ADR-0279) under 8
# characters, and with a control character; UNOS itself refuses a new password over 72 bytes
# (MAX_PASSWORD_BYTES of src/auth/schemas.py, ADR-0315) with 422.
SMB_TOO_SHORT = "SMB passwords must have at least 8 characters"
SMB_CONTROL = "SMB passwords must not contain control characters such as tabs or line breaks"
UNOS_PASSWORD_BYTES = 72
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
for t in getent getfacl pdbedit setfacl smbclient stdbuf testparm; do
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
# share. The first line of standard input is the password: it goes into an auth file of mode 600
# in a private folder in RAM, deleted at the end, with a client configuration that requires
# signing (client signing for a share, client ipc signing for the IPC$ of a share list) and
# keeps smbclient's own files there. list and anonymous print "end <status> <output in
# base64>". session and guest read commands from standard input and pass them to one smbclient
# session; "pause N" waits until smbclient has run what came before and prints "at N <its
# output>", or "ended N <output>" when smbclient has ended; after the last line, "end <status>
# <output>".
REMOTE_SMB = r'''
set -uo pipefail
trap "" PIPE
mode=$1 user=$2 share=$3
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
    for d in "cache directory" "lock directory" "state directory" "private dir" "pid directory" "ncalrpc dir"; do
        echo "   $d = $dir"
    done
    echo "   log level = 0"
} > smb.conf
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

# The smbconf check's edit of smb.conf by hand (ADR-0309), without the read-only namespace: it
# writes /etc/samba. $1: edit or restore; $2: the check's section, unoscheck<6 hex digits>hand;
# $3 (edit): smb.conf's digest as the check read it. edit keeps smb.conf in RAM and rewrites it in
# place, as an editor that keeps the file's inode, owner and mode: UNOS's header replaced by a
# comment and the section appended, which Samba does not serve (available = no). It refuses when
# smb.conf changed since the check read it or when smb.conf.unos-replaced exists, which an apply
# would replace. restore ($3: the digest of the check's edited smb.conf, or nothing) puts the
# kept smb.conf back when smb.conf does not start with UNOS's header (after an apply it holds
# UNOS's newer rendering, which stays), deletes the copy in RAM, and deletes
# smb.conf.unos-replaced only when it holds exactly the check's edited file. Prints
# "conf <digest>" and "copy <digest>" or "copy missing".
REMOTE_HAND_EDIT = r'''
set -uo pipefail
action=$1 section=$2 expect=${3:-}
if ! [[ $section =~ ^unoscheck[0-9a-f]{6}hand$ ]]; then
    echo "not a section of a scenario check: $section" >&2
    exit 2
fi
dir=${UNOS_DEV_SAMBA:-/etc/samba}
conf=$dir/smb.conf
copy=$dir/smb.conf.unos-replaced
saved=${UNOS_DEV_SHM:-/dev/shm}/unos-scenario-$section.conf
header="# Generated by UNOS from its database."
digest() { sha256sum < "$1" | cut -d " " -f 1; }
case $action in
    edit)
        [ "$(digest "$conf")" = "$expect" ] || { echo "smb.conf changed since the check read it" >&2; exit 1; }
        [ ! -e "$copy" ] || { echo "$copy exists: an apply would replace it" >&2; exit 1; }
        [ ! -e "$saved" ] || { echo "$saved exists" >&2; exit 1; }
        cp -p "$conf" "$saved" || exit 1
        {
            echo "# Edited by hand for a scenario check of UNOS" &&
            awk 'body || !/^#/ { body = 1; print }' "$saved" &&
            printf "\n[%s]\n   path = /srv/%s\n   available = no\n" "$section" "$section"
        } > "$saved.new" || { rm -f "$saved" "$saved.new"; exit 1; }
        # A write that fails half-way leaves smb.conf without UNOS's header: restore puts it back.
        cat "$saved.new" > "$conf" || { rm -f "$saved.new"; exit 1; }
        rm -f "$saved.new"
        ;;
    restore)
        if [ -f "$saved" ] && [ "$(head -c "${#header}" "$conf" 2> /dev/null)" != "$header" ]; then
            cat "$saved" > "$conf" || exit 1
        fi
        rm -f "$saved"
        if [ -n "$expect" ] && [ -f "$copy" ] && [ "$(digest "$copy")" = "$expect" ]; then
            rm -f "$copy" || exit 1
        fi
        ;;
    *) echo "not an action: $action" >&2; exit 2 ;;
esac
echo "conf $(digest "$conf")"
if [ -f "$copy" ]; then echo "copy $(digest "$copy")"; else echo "copy missing"; fi'''

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
    mkdir -- "$path" && mkdir -- "$path/theirs" &&
        chown root:root "$path" && chown "$other" "$path/theirs" && chmod 01777 "$path" "$path/theirs" || exit 1
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


# --- results ---------------------------------------------------------------------------------


class Failed(Exception):
    """A step's failure, with what went wrong."""


class Stop(Exception):
    """Ends the steps of a check after a FAIL."""


def b64text(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def unb64(text: str) -> str:
    return base64.b64decode(text.encode()).decode("utf-8", "replace") if text else ""


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

    def _listing(self, mode: str, user: str, line: str) -> SmbListing:
        out = self.run(REMOTE_SMB, mode, user, "", data=line + "\n").strip().split(" ")
        if len(out) < 2 or out[0] != "end":
            raise Failed("the SMB helper on the device gave no answer")
        return SmbListing(int(out[1]) if out[1].isdigit() else -1, unb64(out[2] if len(out) > 2 else ""))

    def smb_list(self, user: str, password: str) -> SmbListing:
        return self._listing("list", user, password)

    def smb_anonymous(self) -> SmbListing:
        return self._listing("anonymous", "", "")

    def smb_session(self, user: str, password: str, share: str) -> SmbSession:
        return SmbSession(self, user, password, share)

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

    def hand_edit(self, action: str, section: str, expect: str = "") -> dict[str, str]:
        """REMOTE_HAND_EDIT, without the read-only namespace: it writes /etc/samba (ADR-0309)."""
        out = self.run(REMOTE_HAND_EDIT, action, section, expect, read_only=False)
        found = dict(line.split(" ", 1) for line in out.splitlines() if line.startswith(("conf ", "copy ")))
        if "conf" not in found or "copy" not in found:
            raise Failed(f"the edit of smb.conf answered {tail(out)!r}")
        return found

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


class SmbSession:
    """One smbclient session on the device (REMOTE_SMB's session mode), held open while the
    check calls the API: run() sends commands and waits for their output."""

    def __init__(self, device: Device, user: str, password: str, share: str, timeout: int = 90,
                 mode: str = "session") -> None:
        self.timeout = timeout
        self.n = 0
        self.ended = False
        self.buffer = b""
        self.errors = tempfile.TemporaryFile()  # the helper's standard error, for a failure's message
        self.proc = subprocess.Popen(
            [str(device.ssh), device.command((mode, user, share))],
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
        """TEXT without a secret, also as JSON writes one (a password with a tab): the longest
        first, so that no part of a longer one is left."""
        found = set(getattr(self.api, "secrets", [])) | {form for p in self.temporary for form in (p, json.dumps(p)[1:-1])}
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
        forms = sorted({form for p in self.temporary for form in (p, json.dumps(p)[1:-1])})
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
    volume, quoted, share = c.run, c.run + "q", c.run + "s"
    vpath = f"{root}/{volume}"

    def volumes() -> list[dict]:
        return c.get(f"/storage/pools/{name}/volumes")

    def state() -> dict:
        return {"the volumes": sorted(v["name"] for v in volumes()),
                "the shares": sorted(s["name"] for s in c.get("/shares/smb"))}

    with c.step("the device holds nothing of an earlier check"):
        c.baseline(state)

    with c.step(f"a volume without a quota is made at {vpath}, listed and a subvolume on the host"):
        c.on_cleanup(f"delete the volume {volume}", lambda: c.delete_volume(name, volume))
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

    with c.step("with the share deleted, Delete removes the volume, also on the host"):
        c.delete_share(share)
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


def smb_global(conf: str) -> dict[str, str]:
    """The [global] lines of smb.conf."""
    found: dict[str, str] = {}
    section = None
    for line in conf.splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line.strip("[]").lower()
        elif section == "global" and "=" in line and not line.startswith(("#", ";")):
            key, _, value = line.partition("=")
            found[" ".join(key.split()).lower()] = value.strip()
    return found


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
                "stdbuf, testparm and pdbedit"):
        status = c.get("/shares/smb-status")
        c.expect(not status.get("problems"), "the Shares page reports: " + "; ".join(status.get("problems") or []))
        facts = c.device.facts()
        missing = sorted({"smbclient", "stdbuf", "testparm", "pdbedit", "getent"} - set(facts["tools"]))
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

    with c.step("a wrong password is refused with NT_STATUS_LOGON_FAILURE, never as a guest, and changes passdb.tdb, "
                "where Samba counts it (ADR-0334)") as s:
        before = passdb_time(c)
        listing = c.device.smb_list(user, wrong)
        c.expect(not listing.ok and listing.status == "NT_STATUS_LOGON_FAILURE", f"it answered {listing.status}")
        after = passdb_time(c)
        c.expect(after != before, f"passdb.tdb is as it was ({after}): Samba did not count the failure")
        if guests:  # restrict anonymous = 1 and map to guest = Bad User: guests may list (ADR-0098)
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


def copied_problem(section: str) -> str:
    """The start of the Shares page's problem after an apply copied SECTION (ADR-0092)."""
    return f"smb.conf had shares that UNOS did not create ({section}); they are kept in {REPLACED_COPY}"


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


def restart_and_wait(c: Check) -> int:
    """Restart the supervisor, and wait for its startup reconcile, which holds smb_lock from
    before the first request (src/main.py): a change of a share that does not exist waits for
    it, answers 404 and writes nothing. The seconds until the container was healthy."""
    took = c.device.restart_supervisor()
    c.call("PATCH", f"/shares/smb/{c.run}none", {}, expect=404)
    return took


def restart_if_named(c: Check, section: str) -> None:
    """The copy's problem stays until the supervisor restarts (ADR-0092): restart it when the
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
    edited: dict[str, str] = {}  # the digest of the hand-edited smb.conf, once it is known

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

    with c.step(f"smb.conf is UNOS's, the Shares page reports no problem, and there is no {REPLACED_COPY}, which an "
                "apply would replace"):
        reported = c.before["the problems on the Shares page"]
        c.expect(not reported, "the Shares page reports: " + "; ".join(reported))
        conf = c.device.read(SMB_CONF) or ""
        c.expect(conf.startswith(SMB_HEADER), "smb.conf does not start with UNOS's header")
        others = foreign_sections(conf)
        c.expect(not others, f"smb.conf holds sections that UNOS did not write ({', '.join(others)}): an apply would "
                             f"copy them into {REPLACED_COPY}, which the check's clean-up deletes")
        c.expect(REPLACED_COPY not in c.before["the files of /etc/samba"], f"/etc/samba/{REPLACED_COPY} exists")

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
        c.on_cleanup(f"put smb.conf back if it is not UNOS's, and delete the check's {REPLACED_COPY}",
                     lambda: c.device.hand_edit("restore", hand, edited.get("conf", "")))
        edited["conf"] = c.device.hand_edit("edit", hand, found or "")["conf"]
        text = c.device.read(SMB_CONF) or ""
        c.expect(not text.startswith(SMB_HEADER) and f"\n[{hand}]\n" in text, "smb.conf is not as edited")

    with c.step("before an apply, the Shares page names smb.conf as not UNOS's (ADR-0249)"):
        problems = problems_of(c)
        c.expect(len(problems) == 1 and problems[0].startswith(CONF_NOT_UNOS), f"it reports {problems}")

    with c.step(f"a new share {share} applies smb.conf: the hand-edited file goes byte for byte into {REPLACED_COPY} "
                f"(ADR-0092), and smb.conf is UNOS's again, with the share's section and without [{hand}]"):
        c.on_cleanup(f"delete the share {share} and its folders", remove_share)
        made = c.post("/shares/smb", {"name": share, "path": folder})
        c.expect(made.get("served") and made.get("version") == 1,
                 f"it is served {made.get('served')}, version {made.get('version')}: {made.get('path_problem')}")
        files = c.device.samba_files()
        copy = files.get(REPLACED_COPY) or {}
        c.expect(copy.get("sha256") == edited["conf"], f"{REPLACED_COPY} is {copy or 'missing'}")
        text = c.device.read(SMB_CONF) or ""
        c.expect(text.startswith(SMB_HEADER) and f"\n[{share}]\n" in text and f"[{hand}]" not in text,
                 "smb.conf is not UNOS's rendering with the share's section")
        c.expect(c.device.facts()["testparm"], "testparm refuses smb.conf")
        stat_is(c, c.device.stat([folder]), folder, "2770 root:unos-smb")
        applied = files["smb.conf"]["sha256"]

    with c.step("the Shares page names the copied section, until the supervisor restarts"):
        problems = problems_of(c)
        c.expect(len(problems) == 1 and problems[0].startswith(copied_problem(hand)), f"it reports {problems}")
        copied = problems[0]

    with c.step("a new comment is saved with the expected version, and a stale version answers 409"):
        changed = c.call("PATCH", f"/shares/smb/{share}", {"expected_version": 1, "comment": "unos scenario check"})
        c.expect(changed.get("comment") == "unos scenario check" and changed.get("version") != 1, f"the share is {changed}")
        c.call("PATCH", f"/shares/smb/{share}", {"expected_version": 1, "comment": "stale"}, expect=409)
        applied = c.device.samba_files()["smb.conf"]["sha256"]

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

    with c.step(f"with the check's {REPLACED_COPY} deleted, a restart of the supervisor writes no file in /etc/samba, "
                "and the Shares page reports no problem") as s:
        c.expect(c.device.hand_edit("restore", hand, edited["conf"])["copy"] == "missing", f"{REPLACED_COPY} is still there")
        before = c.device.samba_files()
        took = restart_and_wait(c)
        after = c.device.samba_files()
        c.expect(after == before, f"the restart {describe(before, after)}")
        problems = problems_of(c)
        c.expect(problems == [], f"the Shares page reports {problems}")
        s.text += f": healthy after about {took} s"


# --- shares: the sticky parent, Repair permissions, the user's own SMB, a guest share ---------


def smb_state(answer: dict) -> str:
    """The state of GET /auth/me/smb's answer, with whether SMB is allowed and the code is asked."""
    account = answer["account"]
    return f"{account['state']}, allowed {account['allowed']}, two-factor code {answer['totp_required']}"


def shares_check(c: Check) -> None:
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

    # shares.repair-permissions (ADR-0155, ADR-0156).
    with c.step(f"the share {share} on a new folder makes the folder 2770 root:unos-smb, and Samba serves it"):
        c.on_cleanup(f"delete {folder} for good", lambda: c.delete_for_good(folder))
        c.on_cleanup(f"delete the share {share}", lambda: c.delete_share(share))
        made = c.post("/shares/smb", {"name": share, "path": folder})
        c.expect(made.get("served"), f"it is not served: {made.get('path_problem')}")
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

    with c.step("Repair permissions gives them the group unos-smb, group read and write, and setgid on the folder, "
                "and keeps their owners") as s:
        result = c.post(f"/shares/smb/{share}/repair-permissions")
        c.expect(result.get("state") == "done" and not result.get("errors"),
                 f"it ended {result.get('state')}: {result.get('message')} {result.get('error_samples')}")
        facts = c.device.stat(sorted(entries) + [folder])
        stat_is(c, facts, folder, "2770 root:unos-smb")
        for path, (want, after) in entries.items():
            if want != after:
                stat_is(c, facts, path, after)
        s.text += f": {result.get('message')}"

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
        if guests:  # map to guest = Bad User: Samba takes a user without an account as a guest (ADR-0098)
            s.text += "; a sign-in is not tried, as the device has guest shares"
        else:
            listing = c.device.smb_list(user, password)
            c.expect(not listing.ok and listing.status == "NT_STATUS_LOGON_FAILURE", f"it answered {listing.status}")
            s.text += f": {listing.status}"

    # shares.read-only-guest-share (ADR-0096, ADR-0098, ADR-0100).
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
    # Samba's srvsvc refuse anonymous callers, so it names no share on the device (2026-11-11),
    # although ADR-0098 says that the share list still works.
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
                        help="the pool of the files, volumes, smb, smbconf, shares, scrub and offsite checks")
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
