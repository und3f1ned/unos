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
           taken, a wrong password and an anonymous session refused, the [global] lines without a
           guest share and of Samba's log in /run/samba, no password of the check in an answer
           or a log, the share's Delete keeping its folder, and the user's Delete removing its
           accounts.
  smbconf  smb.conf edited by hand and what the Shares page says (ADR-0309): a share section
           added by hand (available = no) and UNOS's header removed, which the Shares page names
           before an apply; a new share whose apply copies that file byte for byte to
           smb.conf.unos-replaced (ADR-0092) and makes smb.conf UNOS's again, and the copy named
           on the Shares page; a comment saved with the expected version and a stale one refused;
           the share's folder replaced by a symbolic link, so that the share is not served, with
           access refused without a new folder and saved with one; Delete keeping the folder with
           its files and smb.conf byte for byte as found; then, with the copy deleted, a restart
           of the supervisor that writes no file in /etc/samba and leaves no problem.

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
a client configuration of its own in RAM, the scan for passwords; only pdbedit runs without that
namespace, since tdbsam opens passdb.tdb read-write, and the smbconf check's edit of smb.conf by
hand, which writes /etc/samba (ADR-0309). The passwords of the temporary user and the
backup's passphrase are made here, go to the device only on standard input (to the relay, and
into an auth file of mode 600 in RAM that smbclient reads), and are never printed. The checks
start no container (ADR-0183); the smbconf check restarts the supervisor's (ADR-0309).

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
from pathlib import Path
from typing import Any, Callable, Iterator

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import api as relay  # noqa: E402  scripts/dev/api.py

SSH = HERE / "ssh.sh"
SD = HERE / "sd.sh"
AREAS = ("files", "volumes", "smart", "backups", "smb", "smbconf")
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
for t in getent pdbedit smbclient stdbuf testparm; do
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
# which a read-only root refuses. Only lists and reads.
REMOTE_SAMBA_USERS = r'''
set -uo pipefail
pdbedit -L 2> /dev/null | cut -d : -f 1 | sed "s/^/user /"
echo "history $(pdbedit -P "password history" 2> /dev/null | tail -n 1)"'''

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
# signing required by the client, so that a refusal can only come from the server) or session;
# $2: the user; $3: the share. The first line of standard input is the password: it goes into an
# auth file of mode 600 in a private folder in RAM, deleted at the end, with a client
# configuration that requires signing (client signing for a share, client ipc signing for the
# IPC$ of a share list) and keeps smbclient's own files there. list and anonymous print
# "end <status> <output in base64>". session reads commands from standard input and passes them
# to one smbclient session; "pause N" waits until smbclient has run what came before and prints
# "at N <its output>", or "ended N <output>" when smbclient has ended; after the last line,
# "end <status> <output>".
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
[ "$mode" != anonymous ] || signing=default
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
if [ "$mode" = anonymous ]; then
    auth=(-N -U%)
else
    printf "username = %s\npassword = %s\n" "$user" "$password" > auth
    auth=(-A "$dir/auth")
fi
password=
printf "unos scenario check\n" > data.txt
printf "unos scenario check, changed\n" > data2.txt
b64() { base64 | tr -d "\n"; }
if [ "$mode" != session ]; then
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

    def passdb_entries(self) -> tuple[list[str], str]:
        """Samba's account names (pdbedit -L) and the password history's value."""
        names, history = [], ""
        for line in self.run(REMOTE_SAMBA_USERS, read_only=False).splitlines():
            key, _, value = line.partition(" ")
            if key == "user":
                names.append(value)
            elif key == "history":
                found = re.search(r"(\d+)\s*$", value)
                history = found.group(1) if found else value
        return sorted(names), history

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

    def restart_supervisor(self) -> int:
        """Restart the supervisor's container; the seconds until it was healthy again."""
        out = self.run(REMOTE_RESTART).split()
        if len(out) != 2 or out[0] != "healthy" or not out[1].isdigit():
            raise Failed(f"the restart answered {' '.join(out)!r}")
        return int(out[1])


class SmbSession:
    """One smbclient session on the device (REMOTE_SMB's session mode), held open while the
    check calls the API: run() sends commands and waits for their output."""

    def __init__(self, device: Device, user: str, password: str, share: str, timeout: int = 90) -> None:
        self.timeout = timeout
        self.n = 0
        self.ended = False
        self.buffer = b""
        self.errors = tempfile.TemporaryFile()  # the helper's standard error, for a failure's message
        self.proc = subprocess.Popen(
            [str(device.ssh), device.command(("session", user, share))],
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

    api: api.DeviceApi (or what has its request(), upload() and secrets); device: Device;
    interrupts: the exceptions that count as an interruption."""

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
        return relay.redact_text(text, list(getattr(self.api, "secrets", [])) + self.temporary)

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

    def secret(self) -> str:
        """A new temporary password: redacted wherever it would be printed."""
        value = secrets.token_urlsafe(18)
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

    def call(self, method: str, path: str, body: Any = None, params: dict | None = None, expect: Any = 200) -> Any:
        expected = (expect,) if isinstance(expect, int) else tuple(expect)
        if not self.inside:
            raise AssertionError(f"the check calls {method} {path} outside a step")
        answer = self.api.request(method, path, json_body=body, params=params)
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

    def get(self, path: str, params: dict | None = None, expect: Any = 200) -> Any:
        return self.call("GET", path, params=params, expect=expect)

    def post(self, path: str, body: Any = None, params: dict | None = None, expect: Any = 200) -> Any:
        return self.call("POST", path, body, params, expect)

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
        """No temporary secret in an answer of the API, nor in the device's logs SOURCES since SINCE."""
        self.expect(not [a for a in self.answers for p in self.temporary if p in a], "an answer of the API holds one")
        found = self.device.scan(self.temporary, since)
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

    with c.step("Samba runs UNOS's smb.conf without problems with a password history of 3, and the device has "
                "smbclient, stdbuf, testparm and pdbedit"):
        status = c.get("/shares/smb-status")
        c.expect(not status.get("problems"), "the Shares page reports: " + "; ".join(status.get("problems") or []))
        facts = c.device.facts()
        missing = sorted({"smbclient", "stdbuf", "testparm", "pdbedit", "getent"} - set(facts["tools"]))
        c.expect(not missing, f"the device lacks {', '.join(missing)} (installing one is an owner action)")
        c.expect(facts["testparm"], "testparm refuses smb.conf")
        history = c.device.passdb_entries()[1]
        c.expect(history == "3", f"Samba's password history is {history!r}, not 3 (ADR-0278)")
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
        lines = [line for line in c.device.facts()["accounts"] if line.split(":")[0] == user]
        c.expect(lines, "getent passwd has no such account")
        fields = lines[0].split(":")
        c.expect(int(fields[2]) in SMB_UIDS and fields[3] == str(SMB_GID) and fields[4] == "UNOS SMB user"
                 and fields[6].endswith("nologin"), f"its account is {lines[0]}")
        c.expect(user in c.device.passdb_entries()[0], "pdbedit -L does not list it")

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
        before = c.device.facts()["passdb"]
        listing = c.device.smb_list(user, password)
        c.expect(not listing.ok and listing.status == "NT_STATUS_LOGON_FAILURE", f"it answered {listing.status}")
        after = c.device.facts()["passdb"]
        c.expect(after == before, f"passdb.tdb changed at {after} (Samba's password history: "
                                  f"{c.device.passdb_entries()[1]})")

    with c.step("the new password signs in"):
        listing = c.device.smb_list(user, newer)
        c.expect(listing.ok and share in listing.disks, f"smbclient -L: {listing.status}, {listing.disks}")

    with c.step("a wrong password is refused with NT_STATUS_LOGON_FAILURE, never as a guest") as s:
        listing = c.device.smb_list(user, wrong)
        c.expect(not listing.ok and listing.status == "NT_STATUS_LOGON_FAILURE", f"it answered {listing.status}")
        if guests:  # restrict anonymous = 1 and map to guest = Bad User: guests may list (ADR-0098)
            s.text += "; a null session is not tried, as the device has guest shares"
        else:
            listing = c.device.smb_anonymous()
            c.expect(not listing.ok and not listing.disks and listing.status in ("NT_STATUS_ACCESS_DENIED", "NT_STATUS_LOGON_FAILURE"),
                     f"the null session answered {listing.status} and listed {listing.disks}")
            s.text += f", and an anonymous (null) session is refused: {listing.status}"

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


CHECKS: dict[str, Callable[[Check], None]] = {
    "files": files_check,
    "volumes": volumes_check,
    "smart": smart_check,
    "backups": backups_check,
    "smb": smb_check,
    "smbconf": smbconf_check,
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
    parser.add_argument("--pool", type=pool_name, help="the pool of the files, volumes, smb and smbconf checks")
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
