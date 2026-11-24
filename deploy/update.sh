#!/bin/bash
#
# UNOS Update Script
# Updates UNOS to a release, a git tag v<x.y.z> (ADR-0050):
#
#   sudo update.sh [v<x.y.z>]   the newest release of origin, or the one named
#
# The release's images come from GHCR, where .github/workflows/release.yml
# publishes them, and are pulled while UNOS keeps running, so a release that
# cannot be had changes nothing; on a host without release images (32-bit ARM,
# deploy.riscv64-release-images) they are built from the release's checkout
# instead. Then the database and the data volume are backed up, the running
# images are tagged :previous, the release is checked out and started. If it
# does not start or does not answer its health check, it is stopped, the
# backup's database is put back, and the previous images and checkout start
# again. Only one update runs at a time (a flock on /run/unos-update.lock).
#
# The in-app updater of System > Updates runs it too
# (supervisor/src/updates/installer.py), without a terminal:
#
#   update.sh --fetch v<x.y.z>     the tag and its images; nothing else changes
#   update.sh --in-app update|rollback --backup <folder> --result <file> v<x.y.z>
#                                  in a transient systemd unit: no question, no
#                                  backup of its own (the supervisor's
#                                  <folder>/unos.db), and how it ended in <file>
#
# A rollback starts an older release with the backup's database, the live
# database's files renamed aside; if that release does not answer, they come
# back with the version it came from.
#

set -e

# Colors, on a terminal only: the in-app updater reads the messages.
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'
if [ ! -t 1 ]; then
    RED='' GREEN='' YELLOW='' BLUE='' NC=''
fi

# Configuration
UNOS_INSTALL_DIR="${UNOS_INSTALL_DIR:-/opt/unos}"
# Where release.yml publishes the images of a release: <registry>/unos-<service>:<x.y.z>
# (ADR-0050). install.sh has the same.
UNOS_REGISTRY="${UNOS_REGISTRY:-ghcr.io/und3f1ned}"
# A release's tag, v<x.y.z> without leading zeros. install.sh has the same.
RELEASE_TAG='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
BACKUP_DIR="/var/lib/unos/backups"
SECRETS_FILE="/etc/unos/secrets.env"
# Seconds the new version gets to answer over HTTP and HTTPS once
# `docker compose up` has returned, before it is rolled back.
HEALTH_TIMEOUT="${UNOS_HEALTH_TIMEOUT:-300}"
# Seconds Docker can take to report the supervisor healthy or unhealthy, at
# most start_period + (retries + 1) x (interval + timeout) of its healthcheck
# in docker-compose.yml, 600 + 4 x (10 + 10): a failed check counts only if it
# started after start_period, so the one that runs then does not, and the
# supervisor is unhealthy after retries more. A check takes up to timeout once
# its process has started, and Docker waits interval after each one. At
# startup the supervisor first mounts the pools the host has not mounted (up
# to 2 minutes each). Same value in install.sh; change both with the
# healthcheck.
SUPERVISOR_START_TIMEOUT=680
# --wait-timeout for `docker compose up`, where Compose has it (set by
# check_compose_wait).
COMPOSE_UP_WAIT=()
# Compose services; their containers are named unos-<service>.
SERVICES=(supervisor webui)
# Locked while an update runs (see lock_update). In /run, where only root can
# create files: in a world-writable directory such as /run/lock or /tmp, any
# local account could create the file first or hold the lock and so block
# every update. /tmp only where /run does not exist (test sandboxes).
LOCK_DIR=/run
LOCK_FILE="$LOCK_DIR/unos-update.lock"
[ -d "$LOCK_DIR" ] || LOCK_FILE=/tmp/unos-update.lock

# The arguments (parse_args): update (by hand), fetch or in-app; for in-app,
# update or rollback, the supervisor's backup folder and the result file.
MODE=update
IN_APP_ACTION=""
REQUESTED_TAG=""
RESULT_FILE=""

# Set while the update runs.
PREVIOUS_REV=""
CURRENT_VERSION="unknown"
NEW_VERSION="unknown"
BACKUP_PATH=""
IMAGE_REFS=()
# The host folder of the supervisor's /data, the unos-data volume (tag_previous_images).
DATA_FOLDER=""
# Where the update is, for on_interrupt: build, restart, rollback or done.
UPDATE_STAGE=""
# The first error, for the result file; ok or failed once a rollback ended.
FIRST_ERROR=""
ROLLBACK_RESULT=""

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() {
    [ -n "$FIRST_ERROR" ] || FIRST_ERROR=$1
    echo -e "${RED}[ERROR]${NC} $1"
}

# A wrong argument: nothing has changed yet.
usage_error() {
    log_error "$1. Nothing was changed."
    echo "  Usage: $0 [v<x.y.z>]"
    exit 1
}

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --fetch) MODE=fetch ;;
            --in-app | --backup | --result)
                [ $# -ge 2 ] || usage_error "$1 needs a value"
                case "$1" in
                    --in-app) MODE=in-app IN_APP_ACTION=$2 ;;
                    --backup) BACKUP_PATH=$2 ;;
                    --result) RESULT_FILE=$2 ;;
                esac
                shift
                ;;
            -*) usage_error "Unknown option $1" ;;
            *)
                [ -z "$REQUESTED_TAG" ] || usage_error "One release at a time, not $REQUESTED_TAG and $1"
                REQUESTED_TAG=$1
                ;;
        esac
        shift
    done
    if [ -n "$REQUESTED_TAG" ] && ! [[ $REQUESTED_TAG =~ $RELEASE_TAG ]]; then
        usage_error "$REQUESTED_TAG is not a release (a tag v<x.y.z>)"
    fi
    if [ "$MODE" != update ] && [ -z "$REQUESTED_TAG" ]; then
        usage_error "--$MODE needs the release"
    fi
    if [ "$MODE" != in-app ]; then
        [ -z "$BACKUP_PATH$RESULT_FILE" ] || usage_error "--backup and --result go with --in-app"
        return 0
    fi
    case "$IN_APP_ACTION" in
        update | rollback) ;;
        *) usage_error "--in-app takes update or rollback, not \"$IN_APP_ACTION\"" ;;
    esac
    [[ $RESULT_FILE == /* ]] || usage_error "--in-app needs --result <absolute file>"
    [ -f "$BACKUP_PATH/unos.db" ] || usage_error "--in-app needs --backup <folder with unos.db>"
}

# The in-app modes, on a host that has release images only (release_images_here).
check_in_app_host() {
    [ "$MODE" = update ] && return 0
    release_images_here && return 0
    log_error "GHCR has no release images for this host's Docker ($(docker version --format '{{.Server.Arch}}' 2>/dev/null || echo unknown); releases publish amd64, arm64 and riscv64): update with $0 by hand, which builds them here. Nothing was changed."
    exit 1
}

# In-app: how the update ended, for the supervisor that follows the unit
# (updater.py): success, rolled-back or failed, then the first error. Written
# whole and renamed into place, readable by root only, also when update.sh
# stops early.
# shellcheck disable=SC2329 # the EXIT trap of main runs it
write_result() {
    local status=$? outcome=failed message=$FIRST_ERROR
    if [ "$status" -eq 0 ]; then
        outcome=success message=""
    elif [ "$ROLLBACK_RESULT" = ok ]; then
        outcome=rolled-back
    elif [ -n "$DATABASE_PROBLEM" ]; then
        message="$message; the rollback to $CURRENT_VERSION failed too: $DATABASE_PROBLEM"
    elif [ "$ROLLBACK_RESULT" = failed ]; then
        message="$message; the rollback to $CURRENT_VERSION failed too: UNOS did not come back"
    fi
    [ "$status" -eq 0 ] || [ -n "$message" ] || message="update.sh ended with status $status"
    (
        umask 077
        printf '%s\n%s\n' "$outcome" "$message" > "$RESULT_FILE.partial" &&
            mv -f "$RESULT_FILE.partial" "$RESULT_FILE"
    ) || echo "Could not write $RESULT_FILE" >&2
}

print_header() {
    echo ""
    echo -e "${BLUE}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║${NC}                     ${GREEN}UNOS Update${NC}                           ${BLUE}║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

check_root() {
    if [ "$EUID" -ne 0 ]; then
        log_error "This script must be run as root"
        exit 1
    fi
}

check_installation() {
    if [ ! -d "$UNOS_INSTALL_DIR" ]; then
        log_error "UNOS is not installed at $UNOS_INSTALL_DIR"
        log_info "Run install.sh first"
        exit 1
    fi
}

# Checked before anything changes: in $(( )) a word such as "abc" counts as 0
# (an immediate rollback) and "5m" aborts the script after the restart.
# Values of 10^9 or more are refused as well: from 2^63 on, bash wraps them to
# a negative number, which Compose 5 refuses as --wait-timeout, in the update
# and then in the rollback (UNOS would stay stopped).
check_health_timeout() {
    case "$HEALTH_TIMEOUT" in
        "" | *[!0-9]* | *[1-9]?????????*)
            log_error "UNOS_HEALTH_TIMEOUT must be a number of seconds below 1000000000, not \"$HEALTH_TIMEOUT\". Nothing was changed."
            exit 1
            ;;
    esac
    # Base 10: a leading zero would make it octal.
    HEALTH_TIMEOUT=$((10#$HEALTH_TIMEOUT))
}

# One update at a time. A second run (another SSH session, or cron and a
# manual run) would interleave its compose up with this one's, and its
# rollback (git reset --hard) would hit this one's build. The lock is held on
# fd 9 and released when the script ends. Opened for appending: its content
# does not matter, and nothing is truncated.
lock_update() {
    if ! command -v flock >/dev/null; then
        log_warn "flock (util-linux) is not installed: nothing stops a second update.sh from running at the same time"
        return 0
    fi
    # Created readable by root only: flock needs no write access, so any
    # account that can open the file could hold the lock.
    local mask
    mask=$(umask)
    umask 077
    if ! exec 9>>"$LOCK_FILE"; then
        umask "$mask"
        log_error "Cannot open the lock file $LOCK_FILE. Nothing was changed."
        exit 1
    fi
    umask "$mask"
    if ! flock -n 9; then
        log_error "Another update.sh is running (it holds $LOCK_FILE). Nothing was changed."
        echo "  Wait until it has finished, then run this script again."
        exit 1
    fi
}

# git and docker run without the lock's fd 9. A process they leave running in
# the background (git's automatic gc or maintenance, for instance) would
# otherwise keep the lock held after this script has ended. Closed with exec in
# a subshell: for `command git ... 9>&-`, bash 3 keeps a copy of fd 9 that git
# inherits.
git() ( exec 9>&-; command git "$@" )
docker() ( exec 9>&-; command docker "$@" )

# Ask a yes/no question. Without a terminal (cron, pipes) the answer is no.
confirm() {
    local reply=""
    if [ -t 0 ]; then
        read -r -n 1 -p "$1 [y/N] " reply || reply=""
        echo
    fi
    [[ $reply =~ ^[Yy]$ ]]
}

# docker compose in the deploy directory, with the optional overrides from
# $SECRETS_FILE (JWT_SECRET, UNOS_HOSTNAME, ...) in its environment, as in the
# systemd unit. The subshell keeps them out of this script. Explicit exits:
# callers also run it as an if condition, where set -e does not apply.
compose() {
    (
        cd "$UNOS_INSTALL_DIR/deploy" || exit 1
        if [ -f "$SECRETS_FILE" ]; then
            set -a
            # shellcheck source=/dev/null # the host's file, written by install.sh
            . "$SECRETS_FILE" || exit 1
            set +a
        fi
        docker compose "$@"
    )
}

# Whether the Compose version $1 (2.26.1, v2.40.3, 2.40.3+ds1-0ubuntu1, ...)
# is older than 2.30. False when it cannot be read.
compose_before_2_30() {
    [[ $1 =~ ^v?([0-9]+)\.([0-9]+) ]] || return 1
    local major=$((10#${BASH_REMATCH[1]})) minor=$((10#${BASH_REMATCH[2]}))
    [ "$major" -lt 2 ] || { [ "$major" -eq 2 ] && [ "$minor" -lt 30 ]; }
}

# `docker compose up` starts webui only once the supervisor is healthy
# (depends_on), and waits for that. --wait-timeout ends the wait: Compose
# 2.17 added the option (for `up --wait` only), 2.30 applies it to this wait.
# Older versions reject it, so it is passed only where `up --help` lists it.
# The limit is SUPERVISOR_START_TIMEOUT, or HEALTH_TIMEOUT if that is longer:
# if Compose gave up before Docker has decided, a slow but healthy update
# would be rolled back, and the rollback's `up` would give up on the previous
# version the same way and leave webui stopped. (With UNOS_HEALTH_TIMEOUT=0
# the limit is SUPERVISOR_START_TIMEOUT; --wait-timeout 0 means no limit.)
check_compose_wait() {
    local help version wait=$SUPERVISOR_START_TIMEOUT
    [ "$HEALTH_TIMEOUT" -le "$wait" ] || wait=$HEALTH_TIMEOUT
    help=$(docker compose up --help 2>/dev/null) || help=""
    version=$(docker compose version --short 2>/dev/null) || version=""
    case "$help" in
        *--wait-timeout*) COMPOSE_UP_WAIT=(--wait-timeout "$wait") ;;
        *) COMPOSE_UP_WAIT=() ;;
    esac
    if [ ${#COMPOSE_UP_WAIT[@]} -eq 0 ] || compose_before_2_30 "$version"; then
        log_warn "Docker Compose ${version:-(version unknown)} cannot limit its wait for the supervisor to become healthy (that needs Compose 2.30 or later): if the new supervisor does not start, \`docker compose up\` may wait longer than $wait seconds."
    fi
}

# Refuse to update a checkout with local changes. This script used to stash
# local changes, which silently replaced a deployed but unmerged change (e.g.
# a security fix) with the upstream code.
check_worktree() {
    cd "$UNOS_INSTALL_DIR" || exit 1
    local changes
    # Respects .gitignore (node_modules, build output, __pycache__, .env, ...).
    changes=$(git status --porcelain)
    if [ -n "$changes" ]; then
        log_error "$UNOS_INSTALL_DIR has local changes. Nothing was changed:"
        # shellcheck disable=SC2001 # sed indents every line of the list
        echo "$changes" | sed 's/^/    /'
        echo ""
        echo "  To keep them:      cd $UNOS_INSTALL_DIR && sudo git add -A && sudo git commit -m 'Local changes'"
        echo "  To set them aside: cd $UNOS_INSTALL_DIR && sudo git stash --include-untracked"
        echo "  Then run this script again."
        exit 1
    fi
}

get_current_version() {
    cd "$UNOS_INSTALL_DIR" || exit 1
    PREVIOUS_REV=$(git rev-parse HEAD)
    CURRENT_VERSION=$(git describe --tags --always 2>/dev/null || echo "unknown")
    log_info "Current version: $CURRENT_VERSION"
}

# The newest release among the tags on standard input, one per line: by
# version (v1.10.0 after v1.9.0). install.sh has the same.
newest_release() {
    grep -E "$RELEASE_TAG" | sort -V | tail -n 1
}

# Whether the release $1 is newer than the version $2.
newer_release() {
    [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n 1)" = "$1" ]
}

# NEW_VERSION: the release named, or the newest of origin. By hand and with
# --fetch its tag alone is fetched: a tag that exists here already must name
# the same commit, or git refuses to move it. --in-app takes the tag that
# --fetch brought, without the network.
choose_release() {
    cd "$UNOS_INSTALL_DIR" || exit 1
    if [ "$MODE" != in-app ]; then
        if [ -z "$REQUESTED_TAG" ]; then
            local tags
            if ! tags=$(git ls-remote --tags --refs origin 'v*'); then
                log_error "Could not ask origin for its releases (git's error is above). Nothing was changed."
                exit 1
            fi
            REQUESTED_TAG=$(awk -F 'refs/tags/' '{ print $2 }' <<< "$tags" | newest_release)
            if [ -z "$REQUESTED_TAG" ]; then
                log_error "origin has no release (a tag v<x.y.z>) to update to. Nothing was changed."
                exit 1
            fi
        fi
        if ! git fetch --quiet --no-tags origin "refs/tags/$REQUESTED_TAG:refs/tags/$REQUESTED_TAG"; then
            log_error "Could not fetch the release $REQUESTED_TAG from origin (git's error is above). Nothing was changed."
            exit 1
        fi
    fi
    if ! git rev-parse --quiet --verify "refs/tags/$REQUESTED_TAG^{commit}" > /dev/null; then
        log_error "There is no release $REQUESTED_TAG in $UNOS_INSTALL_DIR. Nothing was changed."
        exit 1
    fi
    NEW_VERSION=$REQUESTED_TAG
    # The release that the version which runs is, or follows: v1.2.0 for
    # v1.2.0-3-g712fc3a, the commits after it.
    local base=${CURRENT_VERSION%-*-g*}
    if [ "$MODE" = fetch ]; then
        # Getting an older release's images changes nothing: the in-app
        # updater's rollback needs those of the version before.
        :
    elif [ "$IN_APP_ACTION" = rollback ]; then
        if ! newer_release "$CURRENT_VERSION" "$NEW_VERSION"; then
            log_error "A rollback goes to a release older than $CURRENT_VERSION, not to $NEW_VERSION. Nothing was changed."
            exit 1
        fi
    elif [ "$NEW_VERSION" = "$CURRENT_VERSION" ] && [ "$MODE" = update ]; then
        log_success "UNOS runs the newest release, $NEW_VERSION"
        exit 0
    elif [[ $base =~ $RELEASE_TAG ]]; then
        if ! newer_release "$NEW_VERSION" "$base"; then
            log_error "$NEW_VERSION is not newer than $CURRENT_VERSION, which runs. Nothing was changed."
            exit 1
        fi
    else
        log_warn "UNOS runs $CURRENT_VERSION, which follows no release: it switches to $NEW_VERSION"
    fi
    log_info "Release: $NEW_VERSION"
}

# Whether GHCR has images for this host's Docker: release.yml builds
# linux/amd64, linux/arm64 and linux/riscv64; 32-bit arm has none
# (deploy.riscv64-release-images). The daemon's architecture, which a pull
# takes, not the kernel's: a 32-bit userland on a 64-bit Arm kernel runs arm
# images. install.sh has the same.
release_images_here() {
    case "$(docker version --format '{{.Server.Arch}}' 2>/dev/null)" in
        amd64 | arm64 | riscv64) return 0 ;;
        *) return 1 ;;
    esac
}

# The image of the service $1 of the release $2 on GHCR (v1.3.0: 1.3.0), and
# the name compose starts it by: its project (deploy, the folder of
# docker-compose.yml) and the service. install.sh has the same.
release_image() { echo "$UNOS_REGISTRY/unos-$1:${2#v}"; }
local_image() { echo "deploy-$1"; }

# The release's images, pulled while UNOS keeps running; those that are here
# already are kept. On a host without release images they are built from the
# release's checkout after the backup (use_release_images).
get_release_images() {
    release_images_here || return 0
    log_info "Getting the images of $NEW_VERSION (UNOS keeps running meanwhile)..."
    local service ref
    for service in "${SERVICES[@]}"; do
        ref=$(release_image "$service" "$NEW_VERSION")
        # Not `docker ... > /dev/null`: bash runs a trap within the redirection of the command it
        # interrupted, and on_interrupt's messages would go there too.
        [ -z "$(docker image inspect --format '{{.Id}}' "$ref" 2> /dev/null)" ] || continue
        if ! docker pull --quiet "$ref"; then
            log_error "Could not pull $ref (docker's error is above). UNOS keeps running $CURRENT_VERSION; nothing was changed."
            exit 1
        fi
    done
    log_success "The images of $NEW_VERSION are here"
}

# SQLite's online backup API, run in the supervisor container: a consistent
# copy of the live database while the supervisor keeps running (a plain copy
# of a database in use can be torn). The live database writes ahead
# (ADR-0306): the copy gets the commits still in unos.db-wal and goes back to
# the rollback journal, one file on its own. The checked copy is written to
# stdout.
backup_database_online() {
    docker exec -i unos-supervisor python - <<'PY'
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

src = Path(os.environ.get("UNOS_DB_PATH") or "/data/unos.db")
# dir=: in /tmp (RAM) or nowhere. Without it, a full /tmp would send the copy
# to /var/tmp, in the container's writable layer on the system disk.
with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR") or "/tmp") as tmp:
    copy = os.path.join(tmp, "unos.db")
    # mode=rw: fail instead of creating an empty database at a wrong path.
    source = sqlite3.connect(src.resolve().as_uri() + "?mode=rw", uri=True, timeout=30)
    try:
        target = sqlite3.connect(copy)
        try:
            source.backup(target)
            target.execute("PRAGMA journal_mode = DELETE")
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                sys.exit("The copy of the database failed its integrity check")
        finally:
            target.close()
    finally:
        source.close()
    with open(copy, "rb") as fh:
        shutil.copyfileobj(fh, sys.stdout.buffer)
PY
}

# Write unos.db (consistent) and data.tar.gz (the rest of the data volume,
# including the JWT signing secret) into the directory $1. Called as an if
# condition, where set -e does not apply: every step is chained with &&.
write_backup() (
    set -o pipefail
    umask 077
    if [ "$(docker inspect --format '{{.State.Running}}' unos-supervisor 2>/dev/null)" = "true" ]; then
        # tar exits with 1 when a file changed while it was read (a JSON
        # state file being rewritten); gzip -t still checks the archive.
        backup_database_online > "$1/unos.db" &&
            { docker exec unos-supervisor tar -C /data --exclude='./unos.db*' -czf - . \
                > "$1/data.tar.gz" || [ $? -eq 1 ]; } &&
            gzip -t "$1/data.tar.gz"
    else
        # Not running: nothing uses the database, so plain copies of its files
        # are consistent. A supervisor that stopped without its checkpoint (a
        # crash, or a kill after docker stop's grace period) left its last
        # commits in unos.db-wal (ADR-0306), which goes beside unos.db.
        docker cp unos-supervisor:/data/unos.db "$1/unos.db" &&
            docker cp unos-supervisor:/data - | gzip > "$1/data.tar.gz" &&
            copy_database_log "$1"
    fi
)

# unos.db-wal from the archive of the data volume in $1 into $1, beside the
# copy of unos.db, when the archive has one: SQLite reads the two together.
copy_database_log() {
    if tar -tzf "$1/data.tar.gz" data/unos.db-wal > /dev/null 2>&1; then
        tar -xzf "$1/data.tar.gz" -C "$1" --strip-components=1 data/unos.db-wal
    fi
}

backup_data() {
    log_info "Backing up the database and the data volume..."

    # The backups contain the JWT signing secret (jwt_secret): anyone who can
    # read them can forge administrator sessions.
    mkdir -p "$BACKUP_DIR"
    chmod 700 "$BACKUP_DIR"

    local dir
    dir="$BACKUP_DIR/update-$(date +%Y%m%d-%H%M%S)"
    # Renamed when complete, so an interrupted backup never looks like one.
    mkdir -m 700 "$dir.partial"
    if write_backup "$dir.partial" && mv "$dir.partial" "$dir"; then
        BACKUP_PATH=$dir
        log_success "Backup created in $dir"
        return 0
    fi

    rm -rf "$dir.partial"
    log_error "The backup failed (see the messages above). Nothing was changed."
    if ! confirm "Update without a backup?"; then
        exit 1
    fi
}

# The release's files: a detached HEAD at its tag.
check_out_release() {
    if ! git -C "$UNOS_INSTALL_DIR" -c advice.detachedHead=false checkout --quiet --detach "refs/tags/$NEW_VERSION"; then
        log_error "Could not check out $NEW_VERSION. UNOS keeps running $CURRENT_VERSION; nothing was changed."
        restore_previous
        exit 1
    fi
    log_success "Checked out $NEW_VERSION"
}

# <repository>:previous for an image reference such as deploy-webui[:tag].
previous_tag() {
    local repo=$1
    case "${repo##*/}" in
        *:*) repo=${repo%:*} ;;
    esac
    echo "$repo:previous"
}

# Tag the images of the current containers as :previous, the rollback anchor.
# Tagged images survive `docker image prune`.
tag_previous_images() {
    log_info "Tagging the current images as :previous..."

    local service ref id
    IMAGE_REFS=()
    for service in "${SERVICES[@]}"; do
        ref=$(docker inspect --format '{{.Config.Image}}' "unos-$service" 2>/dev/null) || ref=""
        id=$(docker inspect --format '{{.Image}}' "unos-$service" 2>/dev/null) || id=""
        if [ -z "$ref" ] || [ -z "$id" ]; then
            log_warn "There is no unos-$service container, so a failed update cannot be rolled back"
            IMAGE_REFS=()
            return 0
        fi
        docker image tag "$id" "$(previous_tag "$ref")"
        IMAGE_REFS+=("$ref")
    done
    DATA_FOLDER=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' unos-supervisor 2>/dev/null) || DATA_FOLDER=""
    log_success "Current images tagged"
}

# Whether the supervisor's container has stopped (or is gone): only then are
# the database's files touched.
supervisor_stopped() {
    [ "$(docker inspect --format '{{.State.Running}}' unos-supervisor 2>/dev/null)" != true ]
}

# The database's files in the data volume, and those of the backup folder $1
# copied next to them (unos.db, and unos.db-wal when it has one), readable by
# root only, under names of their own: the rename into place is all that is
# left to do.
DATABASE_FILES=(unos.db unos.db-wal unos.db-shm unos.db-journal)
STAGED=.unos-update
stage_database() {
    local dir=$DATA_FOLDER
    [ -n "$dir" ] && [ -d "$dir" ] && [ -f "$1/unos.db" ] || return 1
    rm -f "$dir/unos.db$STAGED" "$dir/unos.db-wal$STAGED"
    if ! (
        umask 077
        cp "$1/unos.db" "$dir/unos.db$STAGED" &&
            { [ ! -f "$1/unos.db-wal" ] || cp "$1/unos.db-wal" "$dir/unos.db-wal$STAGED"; }
    ); then
        rm -f "$dir/unos.db$STAGED" "$dir/unos.db-wal$STAGED"
        return 1
    fi
}
install_staged_database() {
    local dir=$DATA_FOLDER
    mv -f "$dir/unos.db$STAGED" "$dir/unos.db" || return 1
    [ ! -f "$dir/unos.db-wal$STAGED" ] || mv -f "$dir/unos.db-wal$STAGED" "$dir/unos.db-wal"
}

# Put the database of the backup folder $1 into the data volume while the
# supervisor is stopped. The live log, its index and a journal go first:
# SQLite would apply the log of the database it replaces to the backup's file
# (ADR-0306).
put_back_database() {
    supervisor_stopped && stage_database "$1" || return 1
    rm -f "$DATA_FOLDER/unos.db-wal" "$DATA_FOLDER/unos.db-shm" "$DATA_FOLDER/unos.db-journal" &&
        install_staged_database
}

# A rollback by the in-app updater renames the live database's files aside in
# the volume (nothing is copied), puts them back when the older release does
# not answer, and removes them once it does. The backup's copy is staged
# first, so that only renames lie between the live database and the backup's.
set_database_aside() {
    local name
    for name in "${DATABASE_FILES[@]}"; do
        [ ! -e "$DATA_FOLDER/$name" ] || mv -f "$DATA_FOLDER/$name" "$DATA_FOLDER/$name.unos-rollback" || return 1
    done
}
take_database_back() {
    local name
    supervisor_stopped && [ -e "$DATA_FOLDER/unos.db.unos-rollback" ] || return 1
    for name in "${DATABASE_FILES[@]}"; do
        rm -f "$DATA_FOLDER/$name" "$DATA_FOLDER/$name$STAGED"
    done
    for name in "${DATABASE_FILES[@]}"; do
        [ ! -e "$DATA_FOLDER/$name.unos-rollback" ] || mv -f "$DATA_FOLDER/$name.unos-rollback" "$DATA_FOLDER/$name" || return 1
    done
}
drop_database_set_aside() {
    local name
    for name in "${DATABASE_FILES[@]}"; do
        rm -f "$DATA_FOLDER/$name.unos-rollback"
    done
}

# The rollback of the in-app updater: UNOS stops, the backup's database is
# staged, the live one goes aside, and the older release starts with the
# backup's.
stop_for_the_backups_database() {
    log_info "Stopping UNOS to put back the database of $BACKUP_PATH..."
    compose down --remove-orphans || true
    if ! supervisor_stopped; then
        rollback "The supervisor did not stop, so the database of $BACKUP_PATH was not put back."
    fi
    if ! { stage_database "$BACKUP_PATH" && set_database_aside && install_staged_database; }; then
        rollback "Could not put back the database of $BACKUP_PATH (the folder of /data: ${DATA_FOLDER:-unknown})."
    fi
}

# The database that ran before the release: by the in-app updater's rollback,
# the files it renamed aside; otherwise the backup's. A failure is named in
# DATABASE_PROBLEM, which makes the rollback a failed one.
DATABASE_PROBLEM=""
restore_database_of_before() {
    if [ "$IN_APP_ACTION" = rollback ]; then
        if [ -e "$DATA_FOLDER/unos.db.unos-rollback" ] && ! take_database_back; then
            DATABASE_PROBLEM="the database of $CURRENT_VERSION could not be taken back from $DATA_FOLDER/unos.db*.unos-rollback"
        fi
    elif [ -z "$BACKUP_PATH" ]; then
        log_warn "No backup: $CURRENT_VERSION starts with the database as $NEW_VERSION left it"
    elif put_back_database "$BACKUP_PATH"; then
        log_info "Put back the database of $BACKUP_PATH"
    else
        DATABASE_PROBLEM="the database of $BACKUP_PATH could not be put back into the folder of /data (${DATA_FOLDER:-unknown}), so $CURRENT_VERSION runs with the database that $NEW_VERSION left"
    fi
    if [ -n "$DATABASE_PROBLEM" ]; then
        log_error "Rollback: $DATABASE_PROBLEM"
    fi
}

# Point the image references back at the :previous images and the checkout
# back at the previous revision, so that compose starts what ran before. The
# checkout had no local changes (check_worktree), so the reset loses nothing.
restore_previous() {
    local ref
    for ref in "${IMAGE_REFS[@]}"; do
        docker image tag "$(previous_tag "$ref")" "$ref" ||
            log_error "Could not tag $(previous_tag "$ref") as $ref"
    done
    # On 32-bit ARM that takes the tag from the web UI's image of the new
    # version, which came from another machine (ADR-0456).
    if [ -n "$(webui_elsewhere_platform)" ]; then
        log_warn "$(webui_elsewhere_platform): deploy-webui:latest is the previous web UI again; load the web UI image of the new version again before the next update.sh."
    fi
    git -C "$UNOS_INSTALL_DIR" reset --hard --quiet "$PREVIOUS_REV" ||
        log_error "Could not reset $UNOS_INSTALL_DIR to $PREVIOUS_REV"
}

# The platform of a host that builds no web UI, and nothing elsewhere: its build
# tools publish no binaries for 32-bit ARM with musl (ADR-0456); riscv64 pulls
# the release's (deploy.riscv64-release-images). Docker's architecture, as for
# release_images_here: a 32-bit userland on a 64-bit Arm kernel (uname aarch64)
# builds arm images too (deploy.armv7-supervisor-build). install.sh has the
# same function.
webui_elsewhere_platform() {
    case "$(docker version --format '{{.Server.Arch}}' 2>/dev/null)" in
        arm) echo linux/arm/v7 ;;
    esac
}

# The release's images under the names compose starts: those pulled from
# GHCR, whose own names then go, so that each release's images do not stay;
# on a host without release images they are built from the checkout.
use_release_images() {
    if ! release_images_here; then
        build_images
        return 0
    fi
    local service ref
    for service in "${SERVICES[@]}"; do
        ref=$(release_image "$service" "$NEW_VERSION")
        if ! docker image tag "$ref" "$(local_image "$service")"; then
            log_error "Could not tag $ref as $(local_image "$service"). UNOS keeps running $CURRENT_VERSION; nothing was changed."
            restore_previous
            exit 1
        fi
        docker image rm "$ref" || true
    done
}

build_images() {
    log_info "Building the images of $NEW_VERSION (UNOS keeps running meanwhile)..."

    # On 32-bit ARM the web UI's image deploy-webui:latest comes from another
    # machine (ADR-0456, docs/INSTALL.md) and only the supervisor is built here.
    local services=() platform
    platform=$(webui_elsewhere_platform)
    if [ -n "$platform" ]; then
        log_warn "$platform: only the supervisor is built. The web UI starts from the image deploy-webui:latest, which comes from another machine (docker buildx build --platform $platform): load that of the new version before the update (docs/INSTALL.md)."
        services=(supervisor)
    fi

    # Build with no cache to ensure fresh build. The supervisor image carries
    # the release's version, which the web interface shows (ADR-0234).
    if ! UNOS_VERSION=$NEW_VERSION compose build --no-cache "${services[@]}"; then
        log_error "Building the new images failed. UNOS keeps running $CURRENT_VERSION; nothing was changed."
        restore_previous
        exit 1
    fi

    log_success "Images built"
}

restart_services() {
    log_info "Restarting UNOS with the new images..."

    # up recreates the containers whose image changed and removes those of
    # services that the release dropped or renamed; webui starts once the
    # supervisor is healthy.
    if ! compose up -d --no-build --remove-orphans "${COMPOSE_UP_WAIT[@]}"; then
        rollback "Starting the new containers failed. If the error above says \"dependency failed to start\", \"is unhealthy\", \"exited\" or \"timeout waiting for dependencies\" (the wording depends on the Compose version), the supervisor stopped or did not become healthy in time: see its log below. If the error mentions an overlapping address pool, see \"Network subnet\" in docs/INSTALL.md."
    fi
}

# Base URL (scheme://host:port) of a published port ($2) of the webui container.
published_url() {
    local binding host
    binding=$(compose port webui "$2" 2>/dev/null | head -n 1)
    [ -n "$binding" ] || return 1
    host=${binding%:*}
    case "$host" in
        0.0.0.0 | "[::]" | "") host=127.0.0.1 ;;
    esac
    echo "$1://$host:${binding##*:}"
}

# The health check must answer over plain HTTP (Caddy forwards it to the
# supervisor) and over HTTPS, the only way to reach the UI.
healthy() {
    local http https
    http=$(published_url http 80) || return 1
    https=$(published_url https 443) || return 1
    # -k: the certificate comes from Caddy's local CA.
    curl -fs -o /dev/null --max-time 5 "$http/api/v1/health" &&
        curl -fsk -o /dev/null --max-time 5 "$https/api/v1/health"
}

wait_for_health() {
    log_info "Waiting for UNOS to answer (up to $HEALTH_TIMEOUT seconds)..."

    local deadline=$((SECONDS + HEALTH_TIMEOUT))
    until healthy; do
        if [ "$SECONDS" -ge "$deadline" ]; then
            echo ""
            return 1
        fi
        echo -n "."
        sleep 3
    done
    echo ""
    log_success "UNOS is running!"
}

# The new version failed: show why, then start the previous images with the
# previous checkout again. Always exits non-zero. The script ignores signals
# from here on: a rollback cut short by Ctrl+C or a closed SSH session would
# leave UNOS stopped.
rollback() {
    trap '' INT TERM HUP
    UPDATE_STAGE=rollback
    log_error "$1"
    echo "Last log lines of the new containers:"
    compose logs --no-color --tail 30 2>&1 | sed 's/^/    /' || true

    if [ ${#IMAGE_REFS[@]} -eq 0 ]; then
        ROLLBACK_RESULT=failed
        log_error "No previous images to roll back to; $UNOS_INSTALL_DIR stays at $NEW_VERSION."
        log_error "Update failed. Backup of the data: ${BACKUP_PATH:-none}"
        exit 1
    fi

    log_warn "Rolling back to $CURRENT_VERSION..."
    compose down --remove-orphans || true
    restore_database_of_before
    restore_previous
    if compose up -d --no-build --force-recreate "${COMPOSE_UP_WAIT[@]}" && wait_for_health; then
        if [ -n "$DATABASE_PROBLEM" ]; then
            ROLLBACK_RESULT=failed
        else
            ROLLBACK_RESULT=ok
        fi
        log_warn "Rolled back: UNOS runs $CURRENT_VERSION again"
    else
        ROLLBACK_RESULT=failed
        log_error "UNOS did not come back after the rollback either. Check: docker ps -a; docker logs unos-supervisor. If the supervisor only needed more time to start, start UNOS again with: sudo systemctl restart unos"
    fi
    log_error "Update to $NEW_VERSION failed. Backup of the data: ${BACKUP_PATH:-none}"
    exit 1
}

# Ctrl+C, a closed SSH session (SIGHUP) or kill while the update runs: undo
# what it changed so far, as after a failed build or health check. Without
# this, a session closed during the build left the checkout at the new
# version while the old one kept running (and the next run reported "already
# up to date"), and one closed during the restart left UNOS stopped.
# shellcheck disable=SC2329 # the traps of main run it
on_interrupt() {
    trap '' INT TERM HUP
    set +e
    if [ "$1" = HUP ]; then
        # The terminal is gone: writing to it fails, and docker may give up
        # on its progress output. Keep the messages in a log instead (root
        # only, like the backups next to it).
        mkdir -p "$BACKUP_DIR" 2>/dev/null && chmod 700 "$BACKUP_DIR" 2>/dev/null
        exec >>"$BACKUP_DIR/update-interrupted.log" 2>&1 || exec >/dev/null 2>&1
        echo "$(date) update.sh: SIGHUP during stage '${UPDATE_STAGE:-none}'"
    fi
    echo ""
    case "$UPDATE_STAGE" in
        build)
            log_error "Interrupted. UNOS keeps running $CURRENT_VERSION; the update is undone."
            restore_previous
            ;;
        restart)
            rollback "Interrupted while UNOS was restarting with $NEW_VERSION."
            ;;
        done)
            log_warn "Interrupted after the update: UNOS runs $NEW_VERSION."
            ;;
        *)
            log_error "Interrupted. Nothing was changed."
            ;;
    esac
    exit 1
}

# Remove dangling images only after the new version is healthy. The :previous
# images stay tagged for a manual rollback.
cleanup_old_images() {
    log_info "Cleaning up old Docker images..."

    docker image prune -f >/dev/null 2>&1 || true

    log_success "Cleanup complete"
}

get_local_ip() {
    ip route get 1 2>/dev/null | awk '{print $7; exit}' || true
}

print_completion() {
    local ip cert_name
    ip=$(get_local_ip)
    cert_name=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' unos-webui 2>/dev/null |
        sed -n 's/^UNOS_HOSTNAME=//p')

    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║${NC}              ${GREEN}UNOS Update Complete!${NC}                       ${GREEN}║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "UNOS $NEW_VERSION is running at ${BLUE}https://${ip:-<server address>}${NC} (http:// redirects there)."
    echo "Its certificate comes from UNOS's own CA and is issued for \"${cert_name:-unos}\"; see docs/INSTALL.md."
    echo "Please refresh your browser (Ctrl+Shift+R) to see changes."
    if [ ${#IMAGE_REFS[@]} -gt 0 ]; then
        echo "The images of $CURRENT_VERSION stay tagged :previous."
    fi
    echo ""
}

# Main
main() {
    trap 'on_interrupt INT' INT
    trap 'on_interrupt TERM' TERM
    trap 'on_interrupt HUP' HUP
    print_header
    check_root
    # The result file of the in-app updater, also when an argument is wrong.
    local arg previous=""
    for arg in "$@"; do
        if [ "$previous" = --result ] && [[ $arg == /* ]]; then
            RESULT_FILE=$arg
            trap write_result EXIT
        fi
        previous=$arg
    done
    parse_args "$@"
    check_in_app_host
    check_health_timeout
    lock_update
    check_installation
    check_worktree
    get_current_version
    choose_release
    get_release_images
    if [ "$MODE" = fetch ]; then
        exit 0
    fi
    # The in-app updater made its backup (ADR-0443): that folder's database
    # is the one a rollback puts back.
    [ "$MODE" = in-app ] || backup_data
    UPDATE_STAGE=build
    tag_previous_images
    check_out_release
    use_release_images
    # Before the restart, so that a rollback has the result too.
    check_compose_wait
    UPDATE_STAGE=restart
    if [ "$IN_APP_ACTION" = rollback ]; then
        stop_for_the_backups_database
    fi
    restart_services
    if ! wait_for_health; then
        # Suggest twice the time it had, at least 600 seconds and at most what
        # check_health_timeout accepts.
        local retry_timeout=$((HEALTH_TIMEOUT * 2))
        [ "$retry_timeout" -ge 600 ] || retry_timeout=600
        [ "$retry_timeout" -lt 1000000000 ] || retry_timeout=999999999
        rollback "UNOS did not answer within $HEALTH_TIMEOUT seconds after the update. If it only starts slowly, retry with: sudo UNOS_HEALTH_TIMEOUT=$retry_timeout $0 $NEW_VERSION"
    fi
    UPDATE_STAGE='done'
    if [ "$IN_APP_ACTION" = rollback ]; then
        drop_database_set_aside
    fi
    cleanup_old_images
    print_completion
}

# One line: bash reads the script while it runs, and the update replaces it.
main "$@"; exit
