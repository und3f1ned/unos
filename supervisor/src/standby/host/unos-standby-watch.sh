#!/bin/sh
# UNOS: the SD card standby takes over when the pool that holds Docker fails (ADR-0486, ADR-0488).
# Run by unos-standby-watch.service on the eMMC's system booted through the card's chooser
# (unos.root=emmc). Docker and with it UNOS stop when their pool fails, so the supervisor cannot
# act: this script does. Every $every seconds it finds the filesystem of Docker's data root, when
# that root lies below the pools' folder: the pool is down when the root filesystem holds it (the
# pool is not mounted) or its filesystem is read-only (btrfs forced it read-only after errors, or
# it lost a device). A pool that is not mounted is mounted from its fstab line first, and Docker
# started, should its disk have come up after systemd gave up on the mount. After $needed checks in a
# row that find the pool down it writes unos/emmc-failed with the reason docker_pool_failed and
# reboots, and the chooser boots the card's root, which runs Docker from the card. It does nothing
# unless the card's btrfs is mounted with the current generation's subvolume and holds no mark. It
# writes nothing while the pool is fine. UNOS writes this file when it sets up the standby; do not
# edit it.
set -eu

# The paths and the pace may be moved for the tests (supervisor/tests/test_standby.py) only.
boot=${UNOS_STANDBY_BOOT:-/mnt/.unos-standby/boot}
card=${UNOS_STANDBY_CARD:-/mnt/.unos-standby/card}
daemon=${UNOS_STANDBY_DAEMON_JSON:-/etc/docker/daemon.json}
pools=${UNOS_STANDBY_POOLS:-/mnt/pools}
every=${UNOS_STANDBY_CHECK_SECONDS:-60}
needed=${UNOS_STANDBY_FAILED_CHECKS:-5}
checks=${UNOS_STANDBY_MAX_CHECKS:-0}

# Docker's data root as daemon.json names it; empty when it is not below the pools' folder.
data_root() {
    root=$(sed -n 's/.*"data-root"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$daemon" 2> /dev/null | head -n 1)
    case $root in
        "${pools%/}"/*) printf '%s\n' "$root" ;;
    esac
}

# Why Docker's pool is down (not_mounted, read_only), or nothing.
failure() {
    root=$(data_root)
    [ -n "$root" ] || return 0
    path=$root
    while [ ! -e "$path" ] && [ "$path" != / ]; do
        path=$(dirname "$path")
    done
    # shellcheck disable=SC2046 # the three columns become the positional parameters
    set -- $(findmnt -n -o TARGET,VFS-OPTIONS,FS-OPTIONS -T "$path" 2> /dev/null || true)
    if [ $# -lt 3 ]; then
        return 0
    fi
    case $1 in
        "${pools%/}"/*) ;;
        *) echo not_mounted; return 0 ;;
    esac
    case "$2,$3" in
        ro,* | *,ro | *,ro,*) echo read_only ;;
    esac
}

# The pool's mount point: the first folder of the data root below the pools' folder.
pool_of() {
    rest=${1#"${pools%/}"/}
    printf '%s/%s\n' "${pools%/}" "${rest%%/*}"
}

# A standby that can take over: the current generation's subvolume on the mounted card, no mark.
ready() {
    gen=$(sed -n 's/^unos_gen=\([0-9][0-9]*\)$/\1/p' "$boot/unos/current.txt" 2> /dev/null | head -n 1)
    [ -n "$gen" ] && [ ! -e "$boot/unos/emmc-failed" ] && mountpoint -q "$card" && [ -d "$card/@sys-$gen" ]
}

switch() {
    echo "unos-standby-watch: Docker's pool is down ($1): the SD card standby takes over" >&2
    printf 'reason=docker_pool_failed\ndetail=%s\ntime=%s\n' "$1" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        > "$boot/unos/emmc-failed.tmp"
    sync "$boot/unos/emmc-failed.tmp"
    mv -f "$boot/unos/emmc-failed.tmp" "$boot/unos/emmc-failed"
    sync "$boot/unos"
    systemctl reboot
}

count=0
done_checks=0
while :; do
    sleep "$every"
    why=$(failure)
    if [ "$why" = not_mounted ]; then
        target=$(pool_of "$(data_root)")
        if mount "$target" 2> /dev/null; then
            echo "unos-standby-watch: $target was not mounted: mounted it, and Docker starts" >&2
            systemctl start docker.service || true
            why=
        fi
    fi
    if [ -n "$why" ] && ready; then
        count=$((count + 1))
    else
        count=0
    fi
    if [ "$count" -ge "$needed" ]; then
        switch "$why"
        exit 0
    fi
    done_checks=$((done_checks + 1))
    if [ "$checks" -gt 0 ] && [ "$done_checks" -ge "$checks" ]; then
        exit 0
    fi
done
