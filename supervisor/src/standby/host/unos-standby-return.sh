#!/bin/sh
# The return from the SD standby's root to the eMMC with the card's changes (system.sd-standby-failback).
# The supervisor writes this script, return.env and the files it names into a folder of /run and runs it
# in the transient unit unos-standby-return, outside the containers that it stops, its own included.
#
# check   the eMMC's root is not mounted and passes fsck.ext4 -n;
# stop    Docker and Samba stop; state.json on the card is the one the supervisor read;
# mount   the eMMC's root at $MOUNT;
# copy    the standby's root into it with the sync's exclusions and without the fix-ups (the eMMC's
#         drop-ins that hold Docker until its pool is mounted are left out too), and UNOS's volumes
#         into the eMMC's Docker volumes, each file they replace on a pool kept in a backup folder; the
#         mount point of a pool removed on the card loses its immutable flag first, and one the eMMC
#         lacks (a pool made on the card) is made, immutable below the pools' folder;
# fstab   the eMMC's /etc/fstab: the standby's with the card's root line replaced by the eMMC's lines;
# verify  a dry run finds nothing left to copy (after one more copy at most), the root unmounted
#         passes fsck.ext4 -f -n;
# finish  drill.txt deleted, the tries counter at 0, state.json without the run, the mark deleted
#         last, then a reboot.
#
# Until the mark is deleted the card's chooser boots the card's root, so a return cut off at any point
# leaves the card's standby as it was. A step that fails, or a stop of the unit, unmounts the eMMC,
# starts Docker and Samba again and writes the result: failed, the step, and why. Argument: the folder.
# UNOS writes this file anew for each return; do not edit it.
set -u

dir=$1
# EMMC, the eMMC's root device; MOUNT, where it is mounted; CARD and SECTOR, the card and the sector of its
# tries counter; MARK, DRILL and STATE, the card's files; STATE_SHA, the digest of state.json as the
# supervisor read it; UNITS, the units that stop; DELAY, the seconds before the first step; POOLS, the
# pools' folder.
# shellcheck source=/dev/null
. "$dir/return.env"

step=check
why=""
stopped=""
mounted=0
rsync_root="rsync -aHAX --numeric-ids --one-file-system --delete --omit-dir-times"

say() {
    echo "unos-standby-return: $*"
}

# The next step, in the journal.
begin() {
    step=$1
    say "step $step"
}

# A command under a deadline: its output goes to the journal, and when it fails its last line, or what
# ended it, is in $why.
try() {
    seconds=$1
    shift
    if out=$(timeout --kill-after=10 "$seconds" "$@" 2>&1); then
        [ -z "$out" ] || printf '%s\n' "$out"
        return 0
    else
        status=$?
    fi
    [ -z "$out" ] || printf '%s\n' "$out"
    if [ "$status" -eq 124 ]; then
        why="$1 did not end within $seconds seconds"
    else
        why=$(printf '%s\n' "$out" | sed '/^[[:space:]]*$/d' | tail -n 1)
        [ -n "$why" ] || why="$1 ended with exit status $status"
    fi
    return 1
}

# The result for the supervisor, written whole.
result() {
    printf '%s\n%s\n%s\n' "$1" "$2" "$3" > "$dir/result.tmp"
    mv -f "$dir/result.tmp" "$dir/result"
    say "$1${2:+ (step $2)}: $3"
}

# Put back what this run changed on the card's system: the eMMC unmounted, Docker and Samba started.
fail() {
    trap - TERM INT HUP
    back=""
    if [ "$mounted" = 1 ]; then
        timeout 120 umount "$MOUNT" || timeout 60 umount -l "$MOUNT" || back="$back; $MOUNT stays mounted"
    fi
    for unit in $stopped; do
        timeout 300 systemctl start "$unit" || back="$back; $unit did not start"
    done
    result failed "$step" "$why${back:-; Docker and Samba run again}"
    exit 1
}

trap 'why="the unit was stopped"; fail' TERM INT HUP

sleep "$DELAY"

say "step check"
if findmnt -rn -S "$EMMC" > /dev/null 2>&1; then
    why="$EMMC is mounted"
    fail
fi
try 900 fsck.ext4 -n "$EMMC" || fail

begin stop
for unit in $UNITS; do
    if systemctl is-active --quiet "$unit"; then
        stopped="$stopped $unit"
        try 300 systemctl stop "$unit" || fail
    fi
done
if [ "$(sha256sum < "$STATE" | cut -d' ' -f1)" != "$STATE_SHA" ]; then
    why="the standby's state.json changed after the supervisor read it: return again"
    fail
fi

begin mount
mkdir -p "$MOUNT" || { why="$MOUNT cannot be made"; fail; }
try 300 mount -t ext4 -o noatime "$EMMC" "$MOUNT" || fail
mounted=1

begin copy
cp "$dir/excludes" "$dir/excludes.all" || { why="the exclusions cannot be read"; fail; }
for folder in docker.service.d containerd.service.d; do
    for file in "$MOUNT/etc/systemd/system/$folder"/*.conf; do
        if [ -f "$file" ] && grep -q 'RequiresMountsFor=' "$file"; then
            echo "/etc/systemd/system/$folder/${file##*/}" >> "$dir/excludes.all"
        fi
    done
done

# A pool that the card removed keeps its mount point on the eMMC, immutable while nothing is mounted on it
# (ADR-0034), which the copy's --delete could not remove.
for point in "$MOUNT$POOLS"/*; do
    [ -d "$point" ] || continue
    [ -e "$POOLS/${point##*/}" ] || chattr -i "$point" 2> /dev/null || true
done

copy() {
    # shellcheck disable=SC2086 # the options, split on purpose
    try 7200 $rsync_root --inplace --no-whole-file --exclude-from="$dir/excludes.all" / "$MOUNT/" || return 1
    tab=$(printf '\t')
    while IFS=$tab read -r source target backup; do
        [ -n "$source" ] || continue
        mkdir -p "$target" || { why="$target cannot be made"; return 1; }
        if [ -n "$backup" ]; then
            try 7200 rsync -aHAX --numeric-ids --delete --backup --backup-dir="$backup" "$source/" "$target/" || return 1
        else
            try 7200 rsync -aHAX --numeric-ids --delete --inplace --no-whole-file "$source/" "$target/" || return 1
        fi
    done < "$dir/volumes"
}

# What a dry run finds left to copy, in $left; status 1 when rsync failed.
left_to_copy() {
    # shellcheck disable=SC2086 # the options, split on purpose
    left=$(timeout 7200 $rsync_root --dry-run --itemize-changes --exclude-from="$dir/excludes.all" / "$MOUNT/") || {
        why="the comparison of the eMMC's root failed"
        return 1
    }
    tab=$(printf '\t')
    while IFS=$tab read -r source target backup; do
        [ -n "$source" ] || continue
        found=$(timeout 7200 rsync -aHAX --numeric-ids --delete --dry-run --itemize-changes "$source/" "$target/") || {
            why="the comparison of $target failed"
            return 1
        }
        left="$left$found"
    done < "$dir/volumes"
}

copy || fail
# The card's mount points that the eMMC lacks: a pool made on the card.
while read -r point; do
    [ -n "$point" ] && [ ! -e "$MOUNT$point" ] || continue
    mkdir -p "$MOUNT$point" || { why="$MOUNT$point cannot be made"; fail; }
    case $point in
        "$POOLS"/*) chattr +i "$MOUNT$point" || { why="$MOUNT$point cannot be made immutable"; fail; } ;;
    esac
done < "$dir/mountpoints"

begin fstab
if ! { cp "$dir/fstab" "$MOUNT/etc/fstab.unos-return" && chmod 0644 "$MOUNT/etc/fstab.unos-return" \
    && sync "$MOUNT/etc/fstab.unos-return" && mv -f "$MOUNT/etc/fstab.unos-return" "$MOUNT/etc/fstab"; }; then
    why="$MOUNT/etc/fstab cannot be written"
    fail
fi

begin verify
left_to_copy || fail
if [ -n "$left" ]; then
    # Something changed during the copy: once more.
    begin copy
    copy || fail
    begin verify
    left_to_copy || fail
    if [ -n "$left" ]; then
        why="the copy does not settle: $(printf '%s\n' "$left" | head -n 1)"
        fail
    fi
fi
sync
try 300 umount "$MOUNT" || fail
mounted=0
try 900 fsck.ext4 -f -n "$EMMC" || fail

# The eMMC holds the card's system: the card's markers go, the mark last.
begin finish
rm -f "$DRILL" || { why="$DRILL cannot be deleted"; fail; }
try 60 dd if="$dir/counter" of="$CARD" bs=512 seek="$SECTOR" count=1 conv=sync,notrunc,fsync status=none || fail
if ! { cp "$dir/state.json" "$STATE.tmp" && sync "$STATE.tmp" && mv -f "$STATE.tmp" "$STATE"; }; then
    why="$STATE cannot be written"
    fail
fi
if ! { rm -f "$MARK" && sync; }; then
    why="$MARK cannot be deleted"
    fail
fi
result returned "" "the eMMC holds the card's system; the NAS reboots to it"
# The eMMC is the system now: the reboot's own stop of this unit puts nothing back.
trap - TERM INT HUP
if ! try 120 systemctl reboot; then
    result failed finish "the reboot did not start: $why; the eMMC holds the card's system: reboot the NAS"
    exit 1
fi
