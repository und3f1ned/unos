#!/usr/bin/env bash
# The job of .github/workflows/btrfs.yml (ADR-0464): pools, volumes, snapshots, a restore and a scrub
# through the API of the real supervisor, on real btrfs on loop devices, checked against what btrfs and
# findmnt show on the runner.
#
#   sudo env GITHUB_ACTIONS=... RUNNER_ENVIRONMENT=... RUNNER_TEMP=... scripts/ci/btrfs-loop.sh PYTHON
#
# PYTHON has the supervisor's runtime packages (supervisor/requirements.txt). The script formats loop
# devices, mounts them below /mnt/pools and writes /etc/fstab, as the supervisor does on a NAS: it runs
# only where nothing is kept after the run, a GitHub-hosted runner (GITHUB_ACTIONS=true and
# RUNNER_ENVIRONMENT=github-hosted), on Linux and as root, and refuses with exit status 2 before it makes
# anything everywhere else.
#
# It makes a folder of its own below RUNNER_TEMP (mode 700) with three sparse files of 1 GiB, each
# attached to a free loop device; starts the supervisor on 127.0.0.1:46190 with the first two as its
# disks (scripts/ci/btrfs_loop.py serve, its log in that folder); and runs the check
# (btrfs_loop.py check), which names the third as a loop device that is no disk of the job and stops
# at once if the supervisor has ended. Its exit status is the check's. A signal that reaches this script
# (INT, TERM, HUP) is passed on to the check as TERM, whose clean-up runs first; on GitHub a cancel or a
# timeout may end the runner before that, and the runner is thrown away. At its exit, also after a
# failure or such a signal: the supervisor stopped (killed if it has not ended 10 s after TERM), its log
# printed in a group of the job's log, what is still mounted from the loop devices unmounted, the
# devices detached and the folder removed; a step of this that fails turns a pass into a failure.
set -euo pipefail

refuse() {
    echo "btrfs-loop.sh: $*" >&2
    exit 2
}

[ $# -eq 1 ] || refuse "usage: $0 PYTHON"
python=$1
if [ "${GITHUB_ACTIONS:-}" != true ] || [ "${RUNNER_ENVIRONMENT:-}" != github-hosted ]; then
    refuse "runs only on a GitHub-hosted runner (GITHUB_ACTIONS=true, RUNNER_ENVIRONMENT=github-hosted)," \
        "which is thrown away after the job: it formats loop devices, mounts them below /mnt/pools and writes /etc/fstab"
fi
[ "$(uname -s)" = Linux ] || refuse "runs only on Linux"
[ "$(id -u)" = 0 ] || refuse "runs only as root (sudo)"

here=$(cd "$(dirname "$0")" && pwd)
port=46190
# The kernel's btrfs, as a module where it is not built in.
grep -qw btrfs /proc/filesystems 2> /dev/null || modprobe btrfs || refuse "the kernel has no btrfs"

state=$(mktemp -d "${RUNNER_TEMP:-/tmp}/unos-btrfs.XXXXXX")
loops=()
supervisor=
checker=

finish() {
    local status=$? loop target stopped=0 undone=yes
    trap - EXIT INT TERM HUP
    # uvicorn ends with the signal that stopped it (143 for TERM): not a failure of the job. A supervisor
    # that ended earlier is waited for too, for its status.
    if [ -n "$supervisor" ]; then
        kill "$supervisor" 2> /dev/null || echo "btrfs-loop.sh: the supervisor had ended before the teardown"
        for _ in $(seq 100); do
            kill -0 "$supervisor" 2> /dev/null || break
            sleep 0.1
        done
        if kill -KILL "$supervisor" 2> /dev/null; then
            echo "btrfs-loop.sh: the supervisor had not stopped 10 s after TERM: killed"
        fi
        wait "$supervisor" 2> /dev/null || stopped=$?
    fi
    echo "::group::The supervisor's log (it stopped with status $stopped)"
    if [ -f "$state/supervisor.log" ]; then
        cat "$state/supervisor.log"
    fi
    echo "::endgroup::"
    for loop in ${loops[@]+"${loops[@]}"}; do
        while read -r target; do
            umount "$target" || undone=no
        done < <(findmnt -rn -S "$loop" -o TARGET)
        losetup -d "$loop" || undone=no
    done
    rm -rf "$state" || undone=no
    if [ "$undone" = no ] && [ "$status" -eq 0 ]; then
        echo "btrfs-loop.sh: the teardown failed" >&2
        status=1
    fi
    exit "$status"
}

# A signal that reaches this script goes on to the check, whose clean-up runs before it ends; then the
# teardown.
stop() {
    # The PIDs of the supervisor and the check are kept by the command after the one that starts each: a
    # signal between the two runs this trap first and finds the PID in $! alone (unset, and unbound for
    # bash 3.2's set -u, before the supervisor starts).
    local last
    set +u
    last=$!
    set -u
    [ -n "$supervisor" ] || supervisor=$last
    [ -n "$checker" ] || [ "$last" = "$supervisor" ] || checker=$last
    if [ -n "$checker" ] && kill -TERM "$checker" 2> /dev/null; then
        wait "$checker" || exit "$1"
    fi
    exit "$1"
}
trap finish EXIT
trap 'stop 130' INT
trap 'stop 143' TERM
trap 'stop 129' HUP

for i in 1 2 3; do
    truncate -s 1G "$state/disk$i.img"
    loop=$(losetup --find --show "$state/disk$i.img")
    loops+=("$loop")
done

"$python" "$here/btrfs_loop.py" serve --state "$state" --port "$port" \
    --disk "${loops[0]}" --disk "${loops[1]}" > "$state/supervisor.log" 2>&1 &
supervisor=$!

# In the background, so that a signal reaches this script's trap while the check runs.
"$python" "$here/btrfs_loop.py" check --url "http://127.0.0.1:$port" \
    --disk "${loops[0]}" --disk "${loops[1]}" --other "${loops[2]}" --supervisor "$supervisor" &
checker=$!
wait "$checker"
