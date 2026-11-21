#!/usr/bin/env bash
# Deploy the working tree to the dev device, one step at a time (ADR-0223).
#
#   scripts/dev/deploy.sh status             the files that differ between the working tree and the device checkout
#   scripts/dev/deploy.sh sync [--dry-run]   make the device checkout's files equal to the working tree's
#   scripts/dev/deploy.sh build-supervisor   build the supervisor image on the device (docker compose build)
#   scripts/dev/deploy.sh build-webui        build the web UI here; on the device only the image's final stage
#   scripts/dev/deploy.sh backup             save the device's fstab, smb.conf and unos-data volume here
#                                            (tmp/dev/backup keeps the newest 3)
#   scripts/dev/deploy.sh up [SERVICE]       backup, docker compose up -d --no-build (SERVICE: supervisor or
#                                            webui), wait until healthy, check the images inside the containers
#   scripts/dev/deploy.sh rollback           back to the files, images and smb.conf of before the last deploy
#   scripts/dev/deploy.sh all                status, sync, build-supervisor, build-webui, up, then check.sh
#
# Every step holds the device lock (ADR-0210) and reports the MiB written to the device's system
# disk, an SD card (ADR-0214). status, sync --dry-run and backup only read there. The other steps
# change what runs on the device: run them when the owner asks for a deploy.
#
# The device checkout (UNOS_DEV_REPO) is compared and synced file by file by tree.py, which reads
# it with the root filesystem read-only (ADR-0221); a sync writes only the files that differ,
# after saving the device's versions of them here (tmp/dev/rollback). The web UI's static files
# are built here from webui/node_modules of make setup, uploaded to the device's RAM, and only
# the final stage of webui/Dockerfile is built there (ADR-0184). A build first tags the previous
# image :rollback. Checks start no container (ADR-0183): after a build they read the image's
# configuration, after up they run inside the running containers. backup (also the first part
# of up) copies the device's state to tmp/dev/backup here, mode 600 (it holds the device's JWT
# secret, password hashes and database), and keeps the newest 3 backups: the one it made and
# the two newest others; older ones and what an interrupted backup left are deleted. Nothing of
# a backup is printed. rollback restores the files of before the last sync, the
# :rollback images and that backup's smb.conf, then starts the containers; the unos-data volume
# and fstab it only names: putting them back is an owner action. tmp/dev is the main
# checkout's, which its git worktrees share (UNOS_DEV_STATE_DIR of env.sh).
#
# Settings: dev.env (see env.sh); UNOS_DEV_UP_TIMEOUT (seconds to wait for health, default 700,
# above the supervisor's 680 s of deploy/docker-compose.yml); UNOS_DEV_PYTHON (python3).

# shellcheck disable=SC2016 # the scripts for the device's shell are in single quotes: it expands them
set -euo pipefail
usage() { awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0"; }
step=${1:-}
case $step in
    -h | --help) usage; exit 0 ;;
    status | sync | build-supervisor | build-webui | backup | up | rollback | all) shift ;;
    *) usage >&2; exit 2 ;;
esac
dry_run=
service=
case $step in
    sync)
        case $# in
            0) ;;
            1) [ "$1" = --dry-run ] || { usage >&2; exit 2; }; dry_run=--dry-run ;;
            *) usage >&2; exit 2 ;;
        esac
        ;;
    up)
        case ${1:-} in
            '' | supervisor | webui) service=${1:-} ;;
            *) usage >&2; exit 2 ;;
        esac
        [ $# -le 1 ] || { usage >&2; exit 2; }
        ;;
    *) [ $# -eq 0 ] || { usage >&2; exit 2; } ;;
esac
. "$(dirname "${BASH_SOURCE[0]}")/env.sh" || exit 1

PYTHON3=${UNOS_DEV_PYTHON:-python3}
TREE=$UNOS_DEV_DIR/tree.py
: "${UNOS_DEV_UP_TIMEOUT:=700}"
[[ $UNOS_DEV_UP_TIMEOUT =~ ^[0-9]+$ ]] || dev_die "UNOS_DEV_UP_TIMEOUT must be a number of seconds"

# --- the scripts that run on the device ($1 is always UNOS_DEV_REPO) --------------------------

# The compose project's name ("deploy", from the directory), which prefixes images and volumes.
REMOTE_PROJECT='project() {
    env -u JWT_SECRET docker compose config --format json | python3 -I -B -c "import json, sys; print(json.load(sys.stdin)[\"name\"])"
}'

REMOTE_BUILD_SUPERVISOR=$REMOTE_PROJECT'
set -euo pipefail
repo=$1
cd "$repo/deploy"
image=$(project)-supervisor
# The bases of supervisor/Dockerfile, pinned by digest (ADR-0305). BuildKit keeps a base that it
# pulled in its build cache, not as an image: buildx du describes those layers as "pulled from
# <name>:<tag>@<digest>".
pulled=$(docker buildx du --verbose 2> /dev/null | sed -n "s/^Description:[[:space:]]*pulled from //p") || true
for base in $(awk '\''toupper($1) == "FROM" { print $2 }'\'' "$repo/supervisor/Dockerfile"); do
    if ! [[ $base =~ @sha256:[0-9a-f]{64}$ ]]; then
        echo "supervisor/Dockerfile names the base $base without a digest (name:tag@sha256:<hex>, ADR-0305)" >&2
        exit 1
    fi
    if docker image inspect "$base" > /dev/null 2>&1 || grep -qF "@${base#*@}" <<< "$pulled"; then
        echo "base $base: on the device"
    else
        # BuildKit also finds a cached base by its layers: a new digest whose image for this
        # platform did not change (another platform was rebuilt) is reused, and no layer of the
        # cache then names it.
        echo "note: no layers of the build cache come from the base $base, so this build pulls it" \
             "and runs apt-get and pip again (hundreds of MB on the SD card) unless the image it names" \
             "for the platform of this device is one the cache holds under another digest." >&2
    fi
done
if docker image inspect "$image:latest" > /dev/null 2>&1; then
    docker tag "$image:latest" "$image:rollback"
fi
# The version the web interface shows (ADR-0234): git describe of the tree on the workstation,
# as $2; the checkout on the device is synced file by file and has no git history.
UNOS_VERSION=${2:-unknown} env -u JWT_SECRET docker compose build supervisor < /dev/null 2>&1 | tail -n 25
docker image inspect -f "$image:latest {{.Id}} created {{.Created}} size {{.Size}}" "$image:latest"
# Static checks of the result: no container is started.
env -u JWT_SECRET docker compose config --quiet && echo "compose config: ok"
if docker image inspect -f "{{range .Config.Env}}{{println .}}{{end}}" "$image:latest" | grep -qx "PYTHONDONTWRITEBYTECODE=1"; then
    echo "image: PYTHONDONTWRITEBYTECODE=1 (no bytecode written to the system disk)"
else
    echo "the image lacks PYTHONDONTWRITEBYTECODE=1" >&2
    exit 1
fi'

REMOTE_BUILD_WEBUI=$REMOTE_PROJECT'
set -euo pipefail
repo=$1 ctx=$2
trap '\''rm -rf "$ctx"'\'' EXIT
cd "$repo/deploy"
[ -f "$ctx/build/index.html" ] && [ -f "$ctx/Caddyfile" ] || { echo "the uploaded build is incomplete" >&2; exit 1; }
cmp -s "$ctx/Caddyfile" "$repo/webui/Caddyfile" || { echo "webui/Caddyfile differs on the device: run deploy.sh sync" >&2; exit 1; }
# The final stage of webui/Dockerfile, with the uploaded static files instead of the builder stage.
awk '\''toupper($1) == "FROM" { stage = "" } { stage = stage $0 "\n" } END { printf "%s", stage }'\'' "$repo/webui/Dockerfile" \
    | sed -E "s#^COPY --from=[^ ]+ /app/build #COPY build #" > "$ctx/Dockerfile"
if grep -q -- "--from=" "$ctx/Dockerfile"; then
    echo "the final stage of webui/Dockerfile copies more than /app/build from another stage" >&2
    exit 1
fi
base=$(awk '\''toupper($1) == "FROM" { print $2; exit }'\'' "$ctx/Dockerfile")
docker image inspect "$base" > /dev/null 2>&1 || { echo "$base is not on the device: the build would pull it (ask the owner)" >&2; exit 1; }
image=$(project)-webui
echo "--- Dockerfile of the final stage"
cat "$ctx/Dockerfile"
echo "---"
if docker image inspect "$image:latest" > /dev/null 2>&1; then
    docker tag "$image:latest" "$image:rollback"
fi
docker build --pull=false -t "$image:latest" "$ctx" < /dev/null 2>&1 | tail -n 15
docker image inspect -f "$image:latest {{.Id}} created {{.Created}} size {{.Size}}" "$image:latest"'

# Run with dev_remote_ro: a tar of /etc/fstab, /etc/samba/smb.conf and the unos-data volume.
REMOTE_BACKUP=$REMOTE_PROJECT'
set -euo pipefail
repo=$1
cd "$repo/deploy"
volume=$(docker volume inspect -f "{{.Mountpoint}}" "$(project)_unos-data")
tar -c -f - --transform "s,^\./,unos-data/," -C / etc/fstab etc/samba/smb.conf -C "$volume" .'

# Run with dev_remote_ro: what the backup belongs to.
REMOTE_BACKUP_META=$REMOTE_PROJECT'
set -euo pipefail
repo=$1
cd "$repo/deploy"
p=$(project)
echo "device checkout HEAD: $(git -c safe.directory="$repo" -C "$repo" rev-parse HEAD)"
for s in supervisor webui; do
    echo "image $p-$s:latest: $(docker image inspect -f "{{.Id}}" "$p-$s:latest" 2> /dev/null || echo none)"
done'

REMOTE_UP='
set -euo pipefail
repo=$1 service=$2 timeout=$3
cd "$repo/deploy"
if [ -n "$service" ]; then
    env -u JWT_SECRET docker compose up -d --no-build "$service" < /dev/null 2>&1 | tail -n 10
    containers=unos-$service
else
    env -u JWT_SECRET docker compose up -d --no-build < /dev/null 2>&1 | tail -n 10
    containers="unos-supervisor unos-webui"
fi
start=$(date +%s)
while :; do
    states= healthy=1
    for c in $containers; do
        s=$(docker inspect -f "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" "$c" 2>/dev/null) || s=missing
        states="$states $c=$s"
        case $s in
            healthy) ;;
            unhealthy | exited | dead | missing) echo "not healthy:$states" >&2; exit 1 ;;
            *) healthy=0 ;;
        esac
    done
    [ "$healthy" = 1 ] && break
    if [ $(( $(date +%s) - start )) -ge "$timeout" ]; then
        echo "not healthy after $timeout s:$states" >&2
        exit 1
    fi
    sleep 3
done
echo "healthy after $(( $(date +%s) - start )) s:$states"
docker ps --filter name=unos --format "{{.Names}} {{.Image}} {{.Status}}"'

# Run with dev_remote_ro after up: the images, checked inside the running containers with docker
# exec (no new container, no bytecode, no pip cache).
REMOTE_POST_UP='
set -uo pipefail
repo=$1
fail=0
read -r -d "" pins << "PY" || true
import importlib.metadata as m, re, sys
bad = 0
for line in sys.stdin:
    found = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*==\s*([^\s;#]+)", line)
    if not found:
        continue
    name, want = found.group(1), found.group(3)
    try:
        have = m.version(name)
    except m.PackageNotFoundError:
        have = "missing"
    if have != want:
        print("  %s: %s in the image, %s in supervisor/requirements.txt" % (name, have, want))
        bad = 1
print("  the pins of supervisor/requirements.txt are installed" if not bad else "  the image differs from supervisor/requirements.txt")
sys.exit(bad)
PY
echo "== unos-supervisor"
docker exec -i -e PYTHONDONTWRITEBYTECODE=1 unos-supervisor python -B -c "$pins" < "$repo/supervisor/requirements.txt" || fail=1
docker exec -e PIP_DISABLE_PIP_VERSION_CHECK=1 -e PIP_NO_CACHE_DIR=1 -e PYTHONDONTWRITEBYTECODE=1 unos-supervisor pip check | sed "s/^/  /" || fail=1
for tool in smartctl btrfs nsenter; do
    docker exec unos-supervisor sh -c "command -v $tool" > /dev/null || { echo "  $tool is missing in the image"; fail=1; }
done
echo "== unos-webui"
docker exec unos-webui caddy version | sed "s/^/  caddy /" || fail=1
docker exec unos-webui grep -qi "content-security-policy" /srv/index.html && echo "  /srv/index.html has its content security policy" \
    || { echo "  /srv/index.html has no content security policy"; fail=1; }
[ "$fail" = 0 ] && echo "post-up check: OK" || { echo "post-up check: FAILED"; exit 1; }'

REMOTE_ROLLBACK_IMAGES=$REMOTE_PROJECT'
set -euo pipefail
repo=$1
cd "$repo/deploy"
p=$(project)
for s in supervisor webui; do
    if docker image inspect "$p-$s:rollback" > /dev/null 2>&1; then
        docker tag "$p-$s:rollback" "$p-$s:latest"
        echo "$p-$s:latest is the image of before the last build again"
    else
        echo "no $p-$s:rollback image: $p-$s:latest stays"
    fi
done'

# smb.conf from a backup on standard input (a tar), replaced atomically, then reloaded.
REMOTE_RESTORE_SMB_CONF='
set -euo pipefail
new=/etc/samba/smb.conf.unos-dev-rollback
tar -x -O -f - etc/samba/smb.conf > "$new"
chmod 644 "$new"
mv "$new" /etc/samba/smb.conf
smbcontrol all reload-config || systemctl reload smbd
echo "smb.conf of the backup restored and reloaded"'

# --- the steps --------------------------------------------------------------------------------

step_status() { "$PYTHON3" "$TREE" status; }

step_sync() { "$PYTHON3" "$TREE" sync ${dry_run:+"$dry_run"}; }

step_build_supervisor() {
    local version
    "$PYTHON3" "$TREE" verify supervisor
    version=$(git -C "$UNOS_REPO_ROOT" describe --tags --always --dirty 2> /dev/null || echo unknown)
    case $version in '' | *[!A-Za-z0-9._+-]*) version=unknown ;; esac
    echo "version: $version"
    dev_remote "$REMOTE_BUILD_SUPERVISOR" "$UNOS_DEV_REPO" "$version"
}

step_build_webui() {
    local webui=$UNOS_REPO_ROOT/webui ctx log
    [ -d "$webui/node_modules" ] || dev_die "webui/node_modules is missing: run make setup"
    "$PYTHON3" "$TREE" verify webui
    mkdir -p "$UNOS_DEV_STATE_DIR"
    log=$UNOS_DEV_STATE_DIR/webui-build.log
    echo "npm run build in webui (log: $log)"
    if ! (cd "$webui" && npm run build) > "$log" 2>&1; then
        tail -n 30 "$log" >&2
        dev_die "npm run build failed"
    fi
    [ -f "$webui/build/index.html" ] || dev_die "npm run build made no build/index.html"
    ctx=/dev/shm/unos-dev-webui-$$-$RANDOM
    (cd "$webui" && COPYFILE_DISABLE=1 tar --no-xattrs -czf - build Caddyfile) \
        | dev_ssh "rm -rf $ctx && mkdir -m 700 $ctx && tar -xzf - --no-same-owner -C $ctx"
    dev_remote "$REMOTE_BUILD_WEBUI" "$UNOS_DEV_REPO" "$ctx"
}

# The backups in UNOS_DEV_STATE_DIR/backup, one directory each, named by the UTC second it was
# made (20261107T120000Z), so that the names sort by time. Each holds the device's secrets:
# step_backup keeps the one it made and the newest others, BACKUPS_KEPT in all.
BACKUPS_KEPT=3
BACKUP_NAME='[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z'

step_backup() {
    local base=$UNOS_DEV_STATE_DIR/backup stamp part old others=0 deleted=0
    (umask 077 && mkdir -p "$base")
    chmod 700 "$base"
    while stamp=$(date -u +%Y%m%dT%H%M%SZ) && [ -e "$base/$stamp" ]; do
        sleep 1
    done
    # Written under another name and renamed when complete, so that an interrupted backup never
    # counts as one: the next backup deletes what it left.
    part=$base/.$stamp.partial
    rm -rf "$part"
    (umask 077 && mkdir "$part")
    if ! dev_remote_ro "$REMOTE_BACKUP" "$UNOS_DEV_REPO" > "$part/device-state.tar" \
        || ! dev_remote_ro "$REMOTE_BACKUP_META" "$UNOS_DEV_REPO" > "$part/meta.txt"; then
        rm -rf "$part"
        dev_die "the backup failed: nothing was saved, and the earlier backups are unchanged"
    fi
    chmod 600 "$part/device-state.tar" "$part/meta.txt"
    mv "$part" "$base/$stamp"
    printf '%s\n' "$stamp" > "$base/latest"
    echo "saved the device's fstab, smb.conf and unos-data volume in $base/$stamp" \
         "(mode 600: it holds the device's JWT secret and password hashes)"
    for old in "$base"/.*.partial; do
        if [ -d "$old" ]; then rm -rf -- "$old"; fi
    done
    for old in "$base"/$BACKUP_NAME; do
        if [ -d "$old" ] && [ "$old" != "$base/$stamp" ]; then others=$((others + 1)); fi
    done
    # The glob lists the oldest first.
    for old in "$base"/$BACKUP_NAME; do
        if [ "$others" -ge "$BACKUPS_KEPT" ] && [ -d "$old" ] && [ "$old" != "$base/$stamp" ]; then
            rm -rf -- "$old"
            others=$((others - 1))
            deleted=$((deleted + 1))
        fi
    done
    if [ "$deleted" -gt 0 ]; then
        echo "older backups deleted: $deleted ($base keeps the newest $BACKUPS_KEPT)"
    fi
}

up_only() {
    dev_remote "$REMOTE_UP" "$UNOS_DEV_REPO" "$service" "$UNOS_DEV_UP_TIMEOUT"
    dev_remote_ro "$REMOTE_POST_UP" "$UNOS_DEV_REPO"
}

step_up() {
    step_backup
    up_only
}

step_rollback() {
    local backup=
    "$PYTHON3" "$TREE" rollback
    dev_remote "$REMOTE_ROLLBACK_IMAGES" "$UNOS_DEV_REPO"
    if [ -f "$UNOS_DEV_STATE_DIR/backup/latest" ]; then
        backup=$UNOS_DEV_STATE_DIR/backup/$(cat "$UNOS_DEV_STATE_DIR/backup/latest")
        dev_ssh "bash -c $(dev_quote "$REMOTE_RESTORE_SMB_CONF")" < "$backup/device-state.tar"
    fi
    service=
    up_only
    if [ -n "$backup" ]; then
        echo "the unos-data volume and /etc/fstab of before the deploy are in $backup/device-state.tar" \
             "(unos-data/, etc/fstab); putting them back is an owner action"
    fi
}

# measured LABEL FUNCTION: run a step with its SD measurement. The step runs in a subshell with
# errexit on: bash ignores set -e inside a function called from a condition, so the status is
# taken after the subshell instead. Never call this from a condition.
measured() {
    local label=$1 rc start
    start=$(dev_sd_sectors) || start=
    set +e
    (
        set -e
        "$2"
    )
    rc=$?
    set -e
    dev_sd_report_from "$start" "$label"
    return "$rc"
}

# --- main
dev_lock "deploy.sh $step"
case $step in
    status) measured "deploy status" step_status ;;
    sync) measured "deploy sync${dry_run:+ --dry-run}" step_sync ;;
    build-supervisor) measured "deploy build-supervisor" step_build_supervisor ;;
    build-webui) measured "deploy build-webui" step_build_webui ;;
    backup) measured "deploy backup" step_backup ;;
    up) measured "deploy up${service:+ $service}" step_up ;;
    rollback) measured "deploy rollback" step_rollback ;;
    all)
        total=$(dev_sd_sectors) || total=
        measured "deploy status" step_status
        measured "deploy sync" step_sync
        measured "deploy build-supervisor" step_build_supervisor
        measured "deploy build-webui" step_build_webui
        measured "deploy up" step_up
        rc=0
        "$UNOS_DEV_DIR/check.sh" || rc=$?
        dev_sd_report_from "$total" "deploy all (total)"
        exit "$rc"
        ;;
esac
