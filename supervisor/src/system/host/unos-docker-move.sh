#!/bin/sh
# UNOS moves Docker's data root and containerd's root onto a pool (ADR-0486, ADR-0487). The supervisor
# writes this script, move.env and the new configuration files (new/) into a folder of /run and runs it
# in the transient unit unos-docker-move, outside the containers that it stops, its own included. It
# stops UNOS's and the apps' containers, Docker and containerd, copies their data into the pool's
# subvolumes, installs the configuration, starts Docker, and once Docker answers from the pool starts
# the containers that ran and deletes the old copies. A step that fails before Docker answers from the
# pool, or a stop of the unit then, puts the old configuration back, starts Docker and the containers as
# before and deletes the pool's copies. The folder's file result says how it ended, for the supervisor to
# alert and audit: moved, restored or failed on its first line; on the second the step that failed, or
# for a move the step whose part did not succeed (a container that did not start, an old copy that
# stays); then the text. Argument: the folder. UNOS writes this file anew for each move; do not edit it.
set -u

dir=$1
# POOL_DIR, the pool's mountpoint; DOCKER_OLD and DOCKER_NEW, Docker's data root now and on the pool;
# CONTAINERD_OLD and CONTAINERD_NEW, containerd's (both empty: containerd's root stays); FILES, the
# configuration as name:target pairs, the file new/<name> to install as target.
# shellcheck source=/dev/null
. "$dir/move.env"

step=prepare
why=""
problems=""
created=""
installed=""
containers=""
unos_stopped=0
stopping=0
services="docker.socket docker.service"
[ -z "$CONTAINERD_NEW" ] || services="$services containerd.service"

say() {
    echo "unos-docker-move: $*"
}

# The result for the supervisor, written whole; the unit's exit status follows it.
finish() {
    printf '%s\n%s\n%s\n' "$1" "$2" "$3" > "$dir/result.tmp"
    mv -f "$dir/result.tmp" "$dir/result"
    say "$1${2:+ (step $2)}: $3"
    case $1 in
        moved) exit 0 ;;
        restored) exit 1 ;;
        *) exit 2 ;;
    esac
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

# Start unos.service again when this run stopped it (its docker compose down removed UNOS's containers,
# which its up makes again), then every container that ran and does not run now. What did not start is
# in $problems.
restart_containers() {
    problems=""
    if [ "$unos_stopped" = 1 ]; then
        try 900 systemctl start unos.service || problems="unos.service: $why"
    fi
    for id in $containers; do
        state=$(timeout 60 docker inspect --format '{{.State.Running}} {{.Name}}' "$id" 2> /dev/null) || continue
        name=${state#* }
        name=${name#/}
        [ "${state%% *}" != true ] || continue
        try 120 docker start "$id" > /dev/null || problems="${problems:+$problems; }$name: $why"
    done
}

# The step $step failed with $1: put Docker back as it was, the old configuration and the old roots,
# start it and the containers that ran, and delete the pool's copies, which are kept when a part of the
# restore failed (Docker may still name them). Never returns.
restore() {
    # A stop of the unit meanwhile does not end the restore, nor the programs it runs.
    trap '' TERM INT HUP
    failure=$1
    say "the step $step failed: $failure; putting Docker back as it was"
    trouble=""
    if [ "$stopping" = 1 ] || [ -n "$installed" ]; then
        # shellcheck disable=SC2086 # one unit per word
        try 300 systemctl stop $services || trouble="stopping Docker: $why"
    fi
    for pair in $installed; do
        name=${pair%%:*}
        target=${pair#*:}
        rm -f "$target.unos-new"
        if [ -e "$dir/old/$name" ]; then
            cp -p "$dir/old/$name" "$target" || trouble="${trouble:+$trouble; }putting back $target failed"
        else
            rm -f "$target" || trouble="${trouble:+$trouble; }removing $target failed"
        fi
    done
    if [ -n "$installed" ]; then
        sync
        try 60 systemctl daemon-reload || trouble="${trouble:+$trouble; }systemctl daemon-reload: $why"
    fi
    if [ "$stopping" = 1 ]; then
        # shellcheck disable=SC2086 # one unit per word
        if try 300 systemctl start $services; then
            restart_containers
            [ -z "$problems" ] || trouble="${trouble:+$trouble; }these did not start again: $problems"
        else
            trouble="${trouble:+$trouble; }starting Docker: $why"
        fi
    fi
    if [ -n "$trouble" ]; then
        finish failed "$step" "$failure; then $trouble${created:+; the copies on the pool were kept:$created}"
    fi
    for subvolume in $created; do
        try 600 btrfs subvolume delete "$subvolume" > /dev/null ||
            trouble="${trouble:+$trouble; }deleting $subvolume: $why"
    done
    [ -z "$trouble" ] || finish failed "$step" "$failure; then $trouble"
    finish restored "$step" "$failure; Docker runs from $DOCKER_OLD as before"
}

# rsync of an old root into its subvolume: hard links, ACLs, extended attributes (overlayfs's) and the
# numeric owners as they are, on its own filesystem, without changing the access times of the files it
# reads, where rsync can. A root with something mounted below it is not copied: the copy would miss it.
copy() {
    [ -d "$1" ] || return 0
    if mounted_below "$1"; then
        why="something is mounted below $1"
        return 1
    fi
    noatime=""
    if rsync --help 2> /dev/null | grep -q -- '--open-noatime'; then
        noatime=--open-noatime
    fi
    # shellcheck disable=SC2086 # no word when rsync cannot
    try 86400 rsync -aHAXx --numeric-ids $noatime "$1/" "$2/" && return 0
    # rsync's first error is the cause, such as a full pool; the sender's broken pipe follows it.
    first=$(printf '%s\n' "$out" | sed -n '/^rsync: /{p;q;}')
    [ -z "$first" ] || why=$first
    return 1
}

# Whether something is mounted below the folder $1 (a bind mount of the same filesystem, which rm's
# --one-file-system would not see): the fifth field of mountinfo is the mount point. The tests
# (supervisor/tests/test_docker_data.py) give a mountinfo of their own.
mountinfo=${UNOS_DOCKER_MOVE_MOUNTINFO:-/proc/self/mountinfo}
mounted_below() {
    [ -r "$mountinfo" ] && awk -v below="$1/" 'index($5, below) == 1 { found = 1 } END { exit !found }' "$mountinfo"
}

# A stop of the unit (systemctl stop, a shutdown) before Docker answers from the pool restores; later, the
# move is done. systemd gives the unit TimeoutStopSec for it.
# shellcheck disable=SC2329 # invoked by the trap below
stopped() {
    case $step in
        restart | delete) finish moved "$step" "Docker's data root is $DOCKER_NEW; the unit was stopped at the step $step" ;;
        *) restore "the unit was stopped" ;;
    esac
}
trap stopped TERM INT HUP

# The paths, checked again here: absolute, the new ones on the pool, the old ones elsewhere.
for path in "$POOL_DIR" "$DOCKER_OLD" "$DOCKER_NEW" "$CONTAINERD_OLD" "$CONTAINERD_NEW"; do
    case $path in
        "" | /*/*) ;;
        *) finish failed "$step" "$path is not a path this move may use" ;;
    esac
done
for path in "$DOCKER_NEW" "$CONTAINERD_NEW"; do
    case $path in
        "" | "$POOL_DIR"/.*) ;;
        *) finish failed "$step" "$path is not on the pool $POOL_DIR" ;;
    esac
done
for path in "$DOCKER_OLD" "$CONTAINERD_OLD"; do
    case $path in
        "$POOL_DIR" | "$POOL_DIR"/*) finish failed "$step" "$path is on the pool $POOL_DIR already" ;;
    esac
done

# 1. The pool is mounted, its two subvolumes are new, and what Docker lists before the move.
mountpoint -q "$POOL_DIR" || restore "$POOL_DIR is not mounted"
for subvolume in "$DOCKER_NEW" "$CONTAINERD_NEW"; do
    [ -n "$subvolume" ] || continue
    [ ! -e "$subvolume" ] || restore "$subvolume exists already"
    try 120 btrfs subvolume create "$subvolume" > /dev/null || restore "$why"
    created="$created $subvolume"
done
listed=$(timeout 120 docker image ls --all --quiet --no-trunc) || restore "Docker did not list its images"
printf '%s\n' "$listed" | sort -u > "$dir/images"

# 2. Every container stops, unos.service's with it, then Docker and containerd.
step=stop
containers=$(timeout 60 docker ps --quiet --no-trunc) || restore "Docker did not list its containers"
stopping=1
if systemctl is-active --quiet unos.service; then
    unos_stopped=1
    try 900 systemctl stop unos.service || restore "$why"
fi
left=$(timeout 60 docker ps --quiet --no-trunc) || restore "Docker did not list its containers"
if [ -n "$left" ]; then
    # shellcheck disable=SC2086 # one container per word
    try 900 docker stop $left > /dev/null || restore "$why"
fi
# shellcheck disable=SC2086 # one unit per word
try 300 systemctl stop $services || restore "$why"

# 3. The copies, on the pool's disks before the configuration names them.
step=copy
copy "$DOCKER_OLD" "$DOCKER_NEW" || restore "$why"
[ -z "$CONTAINERD_NEW" ] || copy "$CONTAINERD_OLD" "$CONTAINERD_NEW" || restore "$why"
sync

# 4. The configuration: each file replaced whole, the one it replaces kept in old/.
step=configure
for pair in $FILES; do
    name=${pair%%:*}
    target=${pair#*:}
    mkdir -p "$(dirname "$target")" || restore "$(dirname "$target") cannot be made"
    if [ -e "$target" ]; then
        cp -p "$target" "$dir/old/$name" || restore "$target cannot be kept"
    fi
    installed="$installed $pair"
    { cp "$dir/new/$name" "$target.unos-new" && chmod 0644 "$target.unos-new" && mv -f "$target.unos-new" "$target"; } ||
        restore "$target cannot be written"
done
sync
try 60 systemctl daemon-reload || restore "$why"

# 5. Docker and containerd start from the pool.
step=start
# shellcheck disable=SC2086 # one unit per word
try 300 systemctl start $services || restore "$why"

# 6. Docker answers from the pool: its data root and containerd's are the pool's, and it lists the images
# it listed before.
step=verify
root=$(timeout 60 docker info --format '{{.DockerRootDir}}') || restore "Docker did not answer"
[ "$root" = "$DOCKER_NEW" ] || restore "Docker's data root is $root, not $DOCKER_NEW"
if [ -n "$CONTAINERD_NEW" ]; then
    dump=$(timeout 60 containerd config dump) || restore "containerd config dump failed"
    root=$(printf '%s\n' "$dump" | sed -n "s/^root = [\"']\(.*\)[\"']\$/\1/p")
    [ "$root" = "$CONTAINERD_NEW" ] || restore "containerd's root is ${root:-not named}, not $CONTAINERD_NEW"
fi
listed=$(timeout 120 docker image ls --all --quiet --no-trunc) || restore "Docker did not list its images"
printf '%s\n' "$listed" | sort -u > "$dir/images.after"
missing=$(comm -23 "$dir/images" "$dir/images.after" | paste -s -d ' ' -)
[ -z "$missing" ] || restore "Docker no longer lists images it listed before the move: $missing"

# 7. The containers that ran start again; one that does not is said in the result, and the move stays.
step=restart
restart_containers

# 8. The old copies go.
step=delete
left=""
for old in "$DOCKER_OLD" "${CONTAINERD_NEW:+$CONTAINERD_OLD}"; do
    [ -n "$old" ] && [ -e "$old" ] || continue
    if mounted_below "$old"; then
        left="${left:+$left; }$old: something is mounted below it"
        continue
    fi
    try 86400 rm -rf --one-file-system -- "$old" || left="${left:+$left; }$old: $why"
done
sync

text="Docker's data root is $DOCKER_NEW"
[ -z "$CONTAINERD_NEW" ] || text="$text, containerd's root $CONTAINERD_NEW"
part=""
[ -z "$problems" ] || { text="$text; these did not start again: $problems"; part=restart; }
[ -z "$left" ] || { text="$text; the old copies that were not deleted: $left"; part=delete; }
finish moved "$part" "$text"
