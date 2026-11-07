#!/usr/bin/env bash
# The backend tests on Linux, in the dev device's RAM: scripts/dev/linux-tests.sh [PYTEST_ARGS...]
#
#   scripts/dev/linux-tests.sh                                   tests/ -q (the whole suite)
#   scripts/dev/linux-tests.sh tests/test_smbconf.py tests/test_host_tools.py -q
#
# For the tests that only run on Linux (/proc, O_TMPFILE, O_PATH, POSIX ACLs; the testparm test
# needs scripts/dev/bin/testparm on PATH instead): CI is the gate,
# this is the quick way to run them before pushing (ADR-0187). The arguments are pytest's,
# relative to supervisor/, as with python -m pytest there.
#
# How: the working tree (tracked and untracked files that git does not ignore) and the
# pure-Python test dependencies the supervisor image lacks (pytest and its plugins, from the
# local venv of make setup) are bundled here by tree.py and unpacked in /dev/shm on the device.
# pytest runs with the Python of the supervisor image, from its filesystem exported once into
# /dev/shm/unos-rootfs (UNOS_DEV_ROOTFS), in a chroot: as nobody (like CI, not root), the root
# read-only, /tmp a tmpfs, no network (a namespace with loopback only), memory limited (3G,
# UNOS_DEV_TEST_MEMORY), stopped after 2400 s (UNOS_DEV_TEST_TIMEOUT). No container is
# created; the device's own root filesystem is read-only for the run (DEV_RO_ROOT of env.sh).
# Under the device lock (ADR-0210); prints the SD writes of the run, which should be about
# zero (ADR-0214). Exits with pytest's status.
#
# Settings: dev.env (see env.sh); PYTHON, the local Python whose packages are bundled
# (supervisor/.venv/bin/python if it exists, else python3).
set -euo pipefail
case ${1:-} in
    -h | --help) awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0"; exit 0 ;;
esac
. "$(dirname "${BASH_SOURCE[0]}")/env.sh" || exit 1

: "${UNOS_DEV_ROOTFS:=/dev/shm/unos-rootfs}"
: "${UNOS_DEV_TEST_TIMEOUT:=2400}"
: "${UNOS_DEV_TEST_MEMORY:=3G}"
[[ $UNOS_DEV_ROOTFS =~ ^/dev/shm/[A-Za-z0-9._-]+$ ]] || dev_die "UNOS_DEV_ROOTFS must be a directory in /dev/shm"
[[ $UNOS_DEV_TEST_TIMEOUT =~ ^[0-9]+$ ]] || dev_die "UNOS_DEV_TEST_TIMEOUT must be a number of seconds"
[[ $UNOS_DEV_TEST_MEMORY =~ ^[0-9]+[KMG]?$ ]] || dev_die "UNOS_DEV_TEST_MEMORY must look like 3G"
[ $# -gt 0 ] || set -- tests/ -q

no_rootfs() {
    cat >&2 << EOF
linux-tests.sh: there is no Python in $UNOS_DEV_ROOTFS on the device (RAM: it is lost at every
reboot). It is the supervisor image's filesystem. Making it creates a container, which writes
about 2 MB to the device's SD card, so it is an owner action:

  scripts/dev/sd.sh measure rootfs -- scripts/dev/ssh.sh 'set -e; c=\$(docker create deploy-supervisor:latest);
    mkdir -p $UNOS_DEV_ROOTFS; docker export "\$c" | tar -x -C $UNOS_DEV_ROOTFS; docker rm "\$c" > /dev/null'

Make it again after the supervisor image changes its Python packages (the run warns when the
rootfs's packages differ from supervisor/requirements.txt).
EOF
    exit 1
}

REMOTE_LIST='
cd "$1/usr/local/lib" 2> /dev/null && [ -x "$1/usr/local/bin/python" ] || exit 3
ls -d python3*/site-packages/*.dist-info
echo "machine=$(uname -m)"'

# Runs on the device (with dev_remote_ro): the chroot of the test run. $1 rootfs, $2 bundle,
# $3 timeout, $4 memory.
REMOTE_RUN='
R=$1 W=$2
printf "127.0.0.1 localhost\n::1 localhost ip6-localhost ip6-loopback\n" > "$R/etc/hosts"
timeout "$3" systemd-run --scope --quiet -p MemoryMax="$4" -p TasksMax=4096 \
    unshare --mount --net --fork --propagation private sh -ec '\''
        R=$1 W=$2
        ip link set lo up
        mount --bind "$R" "$R"
        mkdir -p "$R/w"
        mount -t tmpfs -o size=3g,mode=1777,nosuid,nodev tmpfs "$R/tmp"
        mount --bind "$W" "$R/w"
        mount -o remount,bind,ro,nosuid,nodev "$R/w"
        mount -t proc -o nosuid,nodev,noexec proc "$R/proc"
        mount -t sysfs -o ro,nosuid,nodev,noexec sysfs "$R/sys"
        mount -t tmpfs -o size=16m,mode=755,nosuid tmpfs "$R/dev"
        for n in null zero full random urandom tty; do
            touch "$R/dev/$n"
            mount --bind "/dev/$n" "$R/dev/$n"
        done
        mkdir "$R/dev/shm"
        mount -t tmpfs -o size=256m,mode=1777,nosuid,nodev tmpfs "$R/dev/shm"
        ln -s /proc/self/fd "$R/dev/fd"
        ln -s /proc/self/fd/0 "$R/dev/stdin"
        ln -s /proc/self/fd/1 "$R/dev/stdout"
        ln -s /proc/self/fd/2 "$R/dev/stderr"
        mount -o remount,bind,ro "$R"
        exec chroot --userspec=65534:65534 "$R" /usr/bin/env -i HOME=/tmp TMPDIR=/tmp LANG=C.UTF-8 \
            PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/w/deps PATH=/usr/local/bin:/usr/bin:/bin \
            python -B /w/run.py
    '\'' sh "$R" "$W" < /dev/null'

# --- main
if [ -z "${PYTHON:-}" ]; then
    PYTHON=python3
    [ -x "$UNOS_REPO_ROOT/supervisor/.venv/bin/python" ] && PYTHON=$UNOS_REPO_ROOT/supervisor/.venv/bin/python
fi
"$PYTHON" -c 'import pytest, pytest_asyncio' 2> /dev/null \
    || dev_die "pytest is not installed for $PYTHON: run make setup, or set PYTHON to the venv's python"
dev_lock "linux-tests.sh"
work=$(mktemp -d "${TMPDIR:-/tmp}/unos-dev-tests.XXXXXX")
dev_on_exit "rm -rf '$work'"
rc=0
dev_remote_ro "$REMOTE_LIST" "$UNOS_DEV_ROOTFS" > "$work/dists" || rc=$?
[ "$rc" -ne 3 ] || no_rootfs
[ "$rc" -eq 0 ] || dev_die "could not list $UNOS_DEV_ROOTFS on the device"
machine=$(sed -n 's/^machine=//p' "$work/dists")
"$PYTHON" "$UNOS_DEV_DIR/tree.py" bundle "$work/bundle.tgz" --rootfs-dists "$work/dists" \
    --machine "${machine:-aarch64}" --skip pytest-cov -- "$@"

run=/dev/shm/unos-dev-tests-$$-$RANDOM
start=$(dev_sd_sectors) || start=
set +e
(
    set -e
    dev_ssh "unshare --mount --propagation private sh -c 'mount -o remount,bind,ro,noatime / && rm -rf $run && mkdir -m 755 $run && tar -xzf - --no-same-owner -C $run && chmod -R a+rX $run'" < "$work/bundle.tgz"
    dev_remote_ro "$REMOTE_RUN" "$UNOS_DEV_ROOTFS" "$run" "$UNOS_DEV_TEST_TIMEOUT" "$UNOS_DEV_TEST_MEMORY"
)
rc=$?
dev_ssh "rm -rf $run"
set -e
dev_sd_report_from "$start" "linux-tests.sh"
echo "pytest exit status: $rc"
exit "$rc"
