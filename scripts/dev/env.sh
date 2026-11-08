# shellcheck shell=bash
# Settings of the dev device and shared helpers for the scripts in scripts/dev. Every script
# there starts with:   . "$(dirname "${BASH_SOURCE[0]}")/env.sh" || exit 1
#
# The settings come from dev.env at the repository root (ADR-0212). A linked git worktree (git
# worktree add) without a dev.env of its own uses the main checkout's, found through git's
# common directory (git rev-parse --git-common-dir). UNOS_DEV_ENV_FILE names another file and
# then wins over both (the tests use it). git ignores dev.env, and it holds the device's
# passwords: it must belong to the current user and have mode 600, and is refused otherwise.
# Format: NAME=value per line, the value taken literally (no $-expansion; one pair of
# surrounding quotes is removed); blank lines and lines starting with # are skipped; a name
# appears once. dev.env.example lists the names.
#
# Settings whose names contain PASS, TOKEN, SECRET, API_KEY or PRIVATE_KEY are secrets. They
# are never loaded into variables or the environment: a script that needs one reads it from
# the file with dev_secret into a pipe (askpass.sh to ssh, api.py on standard input), never
# onto a command line, and never prints it. The other settings become exported variables;
# one already set in the environment wins over the file.
#
# Written for bash 3.2 (macOS /bin/bash) as well as current bash.

UNOS_DEV_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd) || return 1
UNOS_REPO_ROOT=$(cd "$UNOS_DEV_DIR/../.." && pwd) || return 1
dev_prog=$(basename "$0")

dev_warn() { printf '%s: %s\n' "$dev_prog" "$*" >&2; }
dev_die() { dev_warn "$@"; exit 1; }

# dev__main_checkout: the root of the main checkout when UNOS_REPO_ROOT is one of its linked
# worktrees: the directory that holds git's common directory (.git). Prints nothing in the main
# checkout itself, for a bare repository or a separate git directory, and without git. The GIT_*
# variables that git exports to its hooks must not point it at another repository.
dev__main_checkout() {
    local common
    common=$(unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE
        git -C "$UNOS_REPO_ROOT" rev-parse --git-common-dir 2> /dev/null) || return 0
    case $common in
        /*) ;;
        ?*) common=$UNOS_REPO_ROOT/$common ;;
        *) return 0 ;;
    esac
    [ "${common##*/}" = .git ] || return 0
    common=$(cd "$common/.." 2> /dev/null && pwd) || return 0
    [ "$common" -ef "$UNOS_REPO_ROOT" ] || printf '%s\n' "$common"
}
dev__main_root=$(dev__main_checkout)

# dev.env: UNOS_DEV_ENV_FILE when it is set; else the one at the root of this checkout; else, in
# a linked worktree, the main checkout's.
dev__env_default=
if [ -z "${UNOS_DEV_ENV_FILE:-}" ]; then
    dev__env_default=1
    UNOS_DEV_ENV_FILE=$UNOS_REPO_ROOT/dev.env
    if [ ! -e "$UNOS_DEV_ENV_FILE" ] && [ -n "$dev__main_root" ] && [ -e "$dev__main_root/dev.env" ]; then
        UNOS_DEV_ENV_FILE=$dev__main_root/dev.env
    fi
fi
export UNOS_DEV_DIR UNOS_REPO_ROOT UNOS_DEV_ENV_FILE

dev__secret_name() {
    case $1 in
        *PASS* | *TOKEN* | *SECRET* | *API_KEY* | *PRIVATE_KEY*) return 0 ;;
    esac
    return 1
}

# The permission bits of a file in octal: GNU stat, else BSD stat.
dev__mode() { stat -L -c %a "$1" 2>/dev/null || stat -L -f %Lp "$1" 2>/dev/null; }

dev__check_env_file() {
    local f=$UNOS_DEV_ENV_FILE mode
    if [ ! -e "$f" ] && [ -n "$dev__env_default" ] && [ -n "$dev__main_root" ]; then
        dev_warn "no dev.env in this worktree ($f) nor in its main checkout ($dev__main_root/dev.env)." \
            "It holds the dev device's settings and passwords and is not in git. Create it in the main" \
            "checkout, which every worktree without its own dev.env uses: 'cp dev.env.example dev.env &&" \
            "chmod 600 dev.env' there (make setup there does this); the owner fills in the passwords." \
            "UNOS_DEV_ENV_FILE names another file."
        return 1
    fi
    if [ ! -e "$f" ]; then
        [ -n "$dev__env_default" ] || f="$f (UNOS_DEV_ENV_FILE)"
        dev_warn "no dev.env at $f. It holds the dev device's settings and passwords and is not in git:" \
            "create it with 'cp dev.env.example dev.env && chmod 600 dev.env' at the repository root" \
            "(make setup does this); the owner fills in the passwords."
        return 1
    fi
    if [ ! -f "$f" ] || [ ! -r "$f" ]; then
        dev_warn "$f is not a readable file."
        return 1
    fi
    if [ ! -O "$f" ]; then
        dev_warn "$f belongs to another user; it holds passwords. Refusing to read it."
        return 1
    fi
    mode=$(dev__mode "$f") || mode=unknown
    if [ "$mode" != 600 ]; then
        dev_warn "$f has mode $mode; it holds passwords, so only mode 600 is accepted. Run: chmod 600 $f"
        return 1
    fi
}

# dev__read MODE: reads dev.env.
#   -          load the settings that are not secrets into exported variables
#   --nul      print every setting as NAME=value followed by a NUL byte (api.py reads this pipe)
#   NAME       print the value of NAME; status 1 if it is missing or empty
# A malformed line is reported by its number only: it may hold a password.
dev__read() {
    local want=$1 line name value isset found= n=0 seen=' '
    local re='^([A-Za-z_][A-Za-z0-9_]*)=(.*)$'
    while IFS= read -r line || [ -n "$line" ]; do
        n=$((n + 1))
        line=${line%$'\r'}
        line=${line#"${line%%[![:space:]]*}"}
        case $line in
            '' | '#'*) continue ;;
            'export '*) line=${line#export }; line=${line#"${line%%[![:space:]]*}"} ;;
        esac
        if [[ ! $line =~ $re ]]; then
            dev_warn "$UNOS_DEV_ENV_FILE line $n is not NAME=value."
            return 2
        fi
        name=${BASH_REMATCH[1]}
        value=${BASH_REMATCH[2]}
        case $seen in
            *" $name "*) dev_warn "$UNOS_DEV_ENV_FILE line $n repeats $name."; return 2 ;;
        esac
        seen="$seen$name "
        case $value in
            \"*\") value=${value#\"}; value=${value%\"} ;;
            \'*\') value=${value#\'}; value=${value%\'} ;;
        esac
        case $want in
            -)
                dev__secret_name "$name" && continue
                eval "isset=\${$name+x}"
                [ -n "$isset" ] && continue
                printf -v "$name" '%s' "$value"
                export "${name?}"
                ;;
            --nul) printf '%s=%s\0' "$name" "$value" ;;
            "$name") found=$value ;;
        esac
    done < "$UNOS_DEV_ENV_FILE"
    case $want in
        - | --nul) return 0 ;;
    esac
    [ -n "$found" ] || return 1
    printf '%s\n' "$found"
}

# dev_secret NAME: print the secret NAME from dev.env (to a pipe, never to a terminal or a log).
dev_secret() {
    dev__secret_name "$1" || { dev_warn "dev_secret: $1 is not a secret's name"; return 2; }
    dev__read "$1"
}

# dev_has_secret NAME: whether dev.env sets NAME to a non-empty value (prints nothing).
dev_has_secret() { dev_secret "$1" > /dev/null; }

dev__check_env_file || return 1
dev__read - || return 1

: "${UNOS_DEV_SSH_USER:=root}"
: "${UNOS_DEV_REPO:=/opt/unos}"
: "${UNOS_DEV_ADMIN_USER:=admin}"
: "${UNOS_DEV_SYSTEM_DISK:=}"
: "${UNOS_DEV_LOCK:=/run/unos-dev.lock}"
: "${UNOS_DEV_LOCK_WAIT:=900}"
: "${UNOS_DEV_LOCK_MAX:=14400}"
# deploy.sh's rollback copies and backups: one record of the one device, kept in the main
# checkout (git ignores tmp/), so that every worktree rolls back the last deploy, whichever
# worktree made it, and removing a worktree does not remove them.
: "${UNOS_DEV_STATE_DIR:=${dev__main_root:-$UNOS_REPO_ROOT}/tmp/dev}"
export UNOS_DEV_SSH_USER UNOS_DEV_REPO UNOS_DEV_ADMIN_USER UNOS_DEV_SYSTEM_DISK UNOS_DEV_LOCK \
    UNOS_DEV_LOCK_WAIT UNOS_DEV_LOCK_MAX UNOS_DEV_STATE_DIR

# These values end up in commands run on the device: allow only plain characters.
dev__valid() {
    local name=$1 re=$2 value
    eval "value=\${$name}"
    [[ $value =~ $re ]] || { dev_warn "$name in $UNOS_DEV_ENV_FILE has unexpected characters or is empty."; return 1; }
}
dev__valid UNOS_DEV_HOST '^[A-Za-z0-9][A-Za-z0-9.:-]*$' || return 1
dev__valid UNOS_DEV_SSH_USER '^[a-z_][a-z0-9_-]*$' || return 1
dev__valid UNOS_DEV_REPO '^/[A-Za-z0-9._/-]+$' || return 1
dev__valid UNOS_DEV_SYSTEM_DISK '^[a-z0-9]*$' || return 1
dev__valid UNOS_DEV_LOCK '^/[A-Za-z0-9._/-]+$' || return 1
dev__valid UNOS_DEV_LOCK_WAIT '^[0-9]+$' || return 1
dev__valid UNOS_DEV_LOCK_MAX '^[0-9]+$' || return 1
case /$UNOS_DEV_REPO/$UNOS_DEV_LOCK/ in
    */../*) dev_warn "UNOS_DEV_REPO and UNOS_DEV_LOCK must not contain '..'."; return 1 ;;
esac

# --- running things on the device ------------------------------------------------------------

dev_ssh() { "$UNOS_DEV_DIR/ssh.sh" "$@"; }

# dev_quote ARGS...: the arguments quoted for the device's shell (bash).
dev_quote() { printf '%q ' "$@"; }

# dev_remote SCRIPT [ARGS...]: run the bash script SCRIPT (text) on the device with ARGS as
# $1...; the script travels on standard input, so the remote command line stays short. It is
# wrapped in { }, so bash reads all of it before running anything: a command in it that reads
# standard input cannot swallow the rest of the script.
dev_remote() {
    local script=$1
    shift
    printf '{\n%s\n}\n' "$script" | dev_ssh "bash -s -- $(dev_quote "$@")"
}

# The device's root filesystem, read-only and without access-time updates, for one command
# (ADR-0221): a private mount namespace in which / is bind-remounted ro,noatime. Only that
# mount's flags change (the system keeps its rw root), and the namespace ends with the command.
# Inside, no program can write to the system disk, not even the access times of the files it
# reads (with relatime, the first read of a file in 24 hours writes its inode); /run, /dev/shm
# and /tmp are RAM and stay writable. Needs root on the device.
DEV_RO_ROOT='mount -o remount,bind,ro,noatime / || { echo "could not make / read-only in a private mount namespace" >&2; exit 1; }'

# dev_remote_ro SCRIPT [ARGS...]: dev_remote, with the root filesystem read-only (DEV_RO_ROOT).
# For everything that only reads.
dev_remote_ro() {
    local script=$1
    shift
    printf '{\n%s\n%s\n}\n' "$DEV_RO_ROOT" "$script" | dev_ssh "unshare --mount --propagation private bash -s -- $(dev_quote "$@")"
}

# --- exit handlers ---------------------------------------------------------------------------

dev__exit_cmds=
dev__on_exit_run() {
    local rc=$?
    set +e
    eval "$dev__exit_cmds"
    exit "$rc"
}
# dev_on_exit COMMAND: run COMMAND when the script exits (last registered runs first).
dev_on_exit() {
    dev__exit_cmds="$1
$dev__exit_cmds"
    trap dev__on_exit_run EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
}

# --- the device lock (ADR-0210) ---------------------------------------------------------------

# The remote side of the lock (with DEV_RO_ROOT): flock on $1 (in /run, RAM), wait up to $2
# seconds for another holder, record the holder (first line of standard input) in $1.owner,
# say "locked", then hold the lock until standard input ends (the workstation's script exits or
# is killed, or the connection drops) or $3 seconds pass.
dev__lock_script=$DEV_RO_ROOT'
exec 9>>"$1" || exit 1
IFS= read -r who || who=unknown
if ! flock -n 9; then
    echo "busy $(cat "$1.owner" 2>/dev/null || echo unknown)"
    flock -w "$2" 9 || { echo "timeout after $2 s"; exit 75; }
fi
printf "%s since %s\n" "$who" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$1.owner"
echo locked
timeout "$3" cat > /dev/null
rm -f "$1.owner"'

# Release: end the session's standard input, so that the device side removes the owner file
# and exits; give it a few seconds, then end the session (with SIGKILL: it ignores HUP and TERM).
dev__unlock() {
    local i
    exec 8>&-
    for i in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$1" 2>/dev/null || break
        sleep 0.5
    done
    kill -KILL "$1" 2>/dev/null
    exec 7<&-
}

# dev_lock [WHAT]: take the device lock for the rest of this script: only one agent deploys
# or runs tests on the device at a time. Scripts started by the holder (UNOS_DEV_LOCK_HELD)
# do not take it again.
dev_lock() {
    local what=${1:-$dev_prog} dir line pid
    if [ -n "${UNOS_DEV_LOCK_HELD:-}" ] && kill -0 "$UNOS_DEV_LOCK_HELD" 2>/dev/null; then
        return 0
    fi
    dir=$(mktemp -d "${TMPDIR:-/tmp}/unos-dev-lock.XXXXXX") || dev_die "mktemp failed"
    mkfifo "$dir/in" "$dir/out" || dev_die "mkfifo failed"
    # The session ignores HUP and TERM (SIGINT a background job ignores anyway, and ssh keeps a
    # signal that it starts with ignored): a closed terminal or a TERM to the script's process
    # group must not release the lock while the script still works, as scenario.sh's checks do
    # while they clean up; the script's exit handler releases it (dev__unlock).
    (trap '' HUP TERM; exec "$UNOS_DEV_DIR/ssh.sh" "unshare --mount --propagation private bash -c $(dev_quote "$dev__lock_script") unos-dev-lock $(dev_quote "$UNOS_DEV_LOCK" \
        "$UNOS_DEV_LOCK_WAIT" "$UNOS_DEV_LOCK_MAX")") < "$dir/in" > "$dir/out" 2>&1 &
    pid=$!
    exec 8> "$dir/in" 7< "$dir/out"
    rm -rf "$dir"
    dev_on_exit "dev__unlock $pid"
    printf '%s (%s@%s, pid %s)\n' "$what" "$(id -un)" "$(hostname -s 2>/dev/null || hostname)" "$$" >&8
    while IFS= read -r -u 7 line; do
        case $line in
            locked)
                UNOS_DEV_LOCK_HELD=$$
                export UNOS_DEV_LOCK_HELD
                return 0
                ;;
            busy*) dev_warn "the device lock is held by ${line#busy }; waiting up to $UNOS_DEV_LOCK_WAIT s" ;;
            *) dev_warn "device lock: $line" ;;
        esac
    done
    dev_die "could not take the device lock $UNOS_DEV_LOCK on $UNOS_DEV_HOST"
}

# --- writes to the device's system disk (ADR-0214) -------------------------------------------

# Sectors (512 bytes) written to the system disk since boot, after a sync (sync flushes every
# filesystem, also from inside the read-only namespace). $1: the disk, else the one that holds
# the root filesystem.
dev__sd_script=$DEV_RO_ROOT'
sync
d=$1
[ -n "$d" ] || d=$(lsblk -no PKNAME "$(findmnt -no SOURCE /)")
awk "{print \$7}" "/sys/block/$d/stat"'

# dev_sd_sectors: the sectors written to UNOS_DEV_SYSTEM_DISK since boot.
dev_sd_sectors() {
    local out
    out=$(dev_ssh "unshare --mount --propagation private sh -c $(dev_quote "$dev__sd_script") sh $(dev_quote "$UNOS_DEV_SYSTEM_DISK")") || return 1
    [[ $out =~ ^[0-9]+$ ]] || { dev_warn "unexpected answer for the system disk's sectors"; return 1; }
    printf '%s\n' "$out"
}

# dev_mib SECTORS: sectors as MiB with three decimals.
dev_mib() { awk -v s="$1" 'BEGIN { printf "%.3f", s * 512 / 1048576 }'; }

dev__sd_start=
# dev_sd_begin: remember the system disk's sectors before a step.
dev_sd_begin() { dev__sd_start=$(dev_sd_sectors) || dev__sd_start=; }

# dev_sd_report LABEL: print (to stderr) the MiB written since dev_sd_begin.
dev_sd_report() { dev_sd_report_from "$dev__sd_start" "$1"; }

# dev_sd_report_from START LABEL: the same since the sectors START (from dev_sd_sectors). With
# UNOS_DEV_SD_LOG set, also append "time label sectors" to that file.
dev_sd_report_from() {
    local start=$1 label=$2 end delta
    if [ -z "$start" ] || ! end=$(dev_sd_sectors); then
        dev_warn "SD writes $label: unknown (the system disk could not be read)"
        return 0
    fi
    delta=$((end - start))
    printf 'SD writes %s: %s MiB (%s sectors)\n' "$label" "$(dev_mib "$delta")" "$delta" >&2
    if [ -n "${UNOS_DEV_SD_LOG:-}" ]; then
        printf '%s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${label// /_}" "$delta" >> "$UNOS_DEV_SD_LOG"
    fi
}
