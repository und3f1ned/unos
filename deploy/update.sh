#!/bin/bash
#
# UNOS Update Script
# Updates UNOS to the latest version of $UNOS_BRANCH.
#
# UNOS keeps running while the new images are built, so a failed build changes
# nothing. The running images are tagged :previous first; if the new version
# does not start or does not answer its health check, the previous images and
# source revision are started again. Only one update runs at a time (a flock
# on /run/unos-update.lock).
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
UNOS_INSTALL_DIR="${UNOS_INSTALL_DIR:-/opt/unos}"
UNOS_BRANCH="${UNOS_BRANCH:-main}"
BACKUP_DIR="/var/lib/unos/backups"
SECRETS_FILE="/etc/unos/secrets.env"
# Seconds the new version gets to answer before it is rolled back. At startup
# the supervisor first mounts the pools the host has not mounted.
HEALTH_TIMEOUT="${UNOS_HEALTH_TIMEOUT:-300}"
# Compose services; their containers are named unos-<service>.
SERVICES=(supervisor webui)
# Locked while an update runs (see lock_update). In /run, where only root can
# create files: in a world-writable directory such as /run/lock or /tmp, any
# local account could create the file first or hold the lock and so block
# every update. /tmp only where /run does not exist (test sandboxes).
LOCK_DIR=/run
LOCK_FILE="$LOCK_DIR/unos-update.lock"
[ -d "$LOCK_DIR" ] || LOCK_FILE=/tmp/unos-update.lock

# Set while the update runs.
PREVIOUS_REV=""
CURRENT_VERSION="unknown"
NEW_VERSION="unknown"
BACKUP_PATH=""
IMAGE_REFS=()
# Where the update is, for on_interrupt: build, restart, rollback or done.
UPDATE_STAGE=""

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

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
check_health_timeout() {
    case "$HEALTH_TIMEOUT" in
        "" | *[!0-9]*)
            log_error "UNOS_HEALTH_TIMEOUT must be a number of seconds, not \"$HEALTH_TIMEOUT\". Nothing was changed."
            exit 1
            ;;
    esac
    # Base 10: a leading zero would make it octal.
    HEALTH_TIMEOUT=$((10#$HEALTH_TIMEOUT))
}

# One update at a time. A second run (another SSH session, or cron and a
# manual run) would interleave its compose down/up with this one's, and its
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
            # shellcheck source=/dev/null
            set -a; . "$SECRETS_FILE" || exit 1; set +a
        fi
        docker compose "$@"
    )
}

# Refuse to update a checkout with local changes or on another branch. This
# script used to stash local changes, which silently replaced a deployed but
# unmerged change (e.g. a security fix) with the upstream code.
check_worktree() {
    cd "$UNOS_INSTALL_DIR" || exit 1
    local branch changes
    branch=$(git symbolic-ref --quiet --short HEAD) || branch=""
    if [ "$branch" != "$UNOS_BRANCH" ]; then
        log_error "$UNOS_INSTALL_DIR is on ${branch:-a detached HEAD}, not on branch $UNOS_BRANCH. Nothing was changed."
        echo "  To update the checked-out branch: sudo UNOS_BRANCH=${branch:-<branch>} $0"
        echo "  To switch to $UNOS_BRANCH:   cd $UNOS_INSTALL_DIR && sudo git checkout $UNOS_BRANCH"
        exit 1
    fi
    # Respects .gitignore (node_modules, build output, __pycache__, .env, ...).
    changes=$(git status --porcelain)
    if [ -n "$changes" ]; then
        log_error "$UNOS_INSTALL_DIR has local changes. Nothing was changed:"
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

check_for_updates() {
    log_info "Checking for updates..."

    cd "$UNOS_INSTALL_DIR" || exit 1
    git fetch --quiet origin "$UNOS_BRANCH"

    local behind ahead
    behind=$(git rev-list --count "HEAD..origin/$UNOS_BRANCH")
    ahead=$(git rev-list --count "origin/$UNOS_BRANCH..HEAD")

    if [ "$ahead" -gt 0 ] && [ "$behind" -gt 0 ]; then
        log_error "$UNOS_BRANCH has $ahead local commit(s) that are not on origin/$UNOS_BRANCH. Nothing was changed."
        echo "  Rebase them onto the update first: cd $UNOS_INSTALL_DIR && sudo git rebase origin/$UNOS_BRANCH"
        echo "  Then run this script again."
        exit 1
    fi

    if [ "$behind" -eq 0 ]; then
        if [ "$ahead" -gt 0 ]; then
            log_success "No new commits on origin/$UNOS_BRANCH ($ahead local commit(s) are not on it)"
        else
            log_success "UNOS is already up to date!"
        fi
        if ! confirm "Force rebuild anyway?"; then
            exit 0
        fi
    else
        log_info "Updates available: $behind commits behind"
    fi
}

# SQLite's online backup API, run in the supervisor container: a consistent
# copy of the live database while the supervisor keeps running (a plain copy
# of a database in use can be torn). The checked copy is written to stdout.
backup_database_online() {
    docker exec -i unos-supervisor python - <<'PY'
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

src = Path(os.environ.get("UNOS_DB_PATH") or "/data/unos.db")
with tempfile.TemporaryDirectory() as tmp:
    copy = os.path.join(tmp, "unos.db")
    # mode=rw: fail instead of creating an empty database at a wrong path.
    source = sqlite3.connect(src.resolve().as_uri() + "?mode=rw", uri=True, timeout=30)
    try:
        target = sqlite3.connect(copy)
        try:
            source.backup(target)
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
        # Not running: nothing uses the database, so a plain copy is consistent.
        docker cp unos-supervisor:/data/unos.db "$1/unos.db" &&
            docker cp unos-supervisor:/data - | gzip > "$1/data.tar.gz"
    fi
)

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

pull_updates() {
    log_info "Pulling updates..."

    cd "$UNOS_INSTALL_DIR" || exit 1
    # check_for_updates made sure that this is a fast-forward.
    git merge --ff-only --quiet "origin/$UNOS_BRANCH"

    NEW_VERSION=$(git describe --tags --always 2>/dev/null || echo "unknown")
    log_success "Updated to: $NEW_VERSION"
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
    log_success "Current images tagged"
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
    git -C "$UNOS_INSTALL_DIR" reset --hard --quiet "$PREVIOUS_REV" ||
        log_error "Could not reset $UNOS_INSTALL_DIR to $PREVIOUS_REV"
}

build_images() {
    log_info "Building the new images (UNOS keeps running meanwhile)..."

    # Build with no cache to ensure fresh build
    if ! compose build --no-cache; then
        log_error "Building the new images failed. UNOS keeps running $CURRENT_VERSION; nothing was changed."
        restore_previous
        exit 1
    fi

    log_success "Images built"
}

restart_services() {
    log_info "Restarting UNOS with the new images..."

    # down, not only up: compose reuses an existing network as it is, and the
    # network of releases without the fixed subnet (172.31.254.0/24) has to be
    # recreated for the containers' fixed addresses.
    if ! compose down --remove-orphans; then
        rollback "Stopping the running containers failed"
    fi
    if ! compose up -d --no-build; then
        rollback "Starting the new containers failed. If the error above mentions an overlapping address pool, see \"Network subnet\" in docs/INSTALL.md."
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
        log_error "No previous images to roll back to; $UNOS_INSTALL_DIR stays at $NEW_VERSION."
        log_error "Update failed. Backup of the data: ${BACKUP_PATH:-none}"
        exit 1
    fi

    log_warn "Rolling back to $CURRENT_VERSION..."
    compose down --remove-orphans || true
    restore_previous
    if compose up -d --no-build --force-recreate && wait_for_health; then
        log_warn "Rolled back: UNOS runs $CURRENT_VERSION again"
    else
        log_error "UNOS did not come back after the rollback either. Check: docker ps -a; docker logs unos-supervisor"
    fi
    log_error "Update to $NEW_VERSION failed. Backup of the data: ${BACKUP_PATH:-none}"
    exit 1
}

# Ctrl+C, a closed SSH session (SIGHUP) or kill while the update runs: undo
# what it changed so far, as after a failed build or health check. Without
# this, a session closed during the build left the checkout at the new
# version while the old one kept running (and the next run reported "already
# up to date"), and one closed between `down` and `up` left UNOS stopped.
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
    check_health_timeout
    lock_update
    check_installation
    check_worktree
    get_current_version
    check_for_updates
    backup_data
    UPDATE_STAGE=build
    tag_previous_images
    pull_updates
    build_images
    UPDATE_STAGE=restart
    restart_services
    if ! wait_for_health; then
        rollback "UNOS did not answer within $HEALTH_TIMEOUT seconds after the update. If it only starts slowly, retry with: sudo UNOS_HEALTH_TIMEOUT=600 $0"
    fi
    UPDATE_STAGE=done
    cleanup_old_images
    print_completion
}

# One line: bash reads the script while it runs, and the update replaces it.
main "$@"; exit
