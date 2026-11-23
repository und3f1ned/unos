#!/bin/sh
# UNOS on the SD card standby's root (ADR-0471), run by unos-standby.service when the kernel's
# command line has unos.root=card. First it keeps the card's root for every later boot: it writes
# unos/emmc-failed with the chooser's reason unless it exists, and deletes unos/drill.txt so that a
# drill acts once. Then it loads the images that the card's Docker lacks, seeds UNOS's compose
# volumes from the copy once per generation, and starts UNOS without building.
# UNOS writes this file into each generation of the card; do not edit it.
set -eu

# The paths may be moved for the tests (supervisor/tests/test_standby.py) only.
boot=${UNOS_STANDBY_BOOT:-/mnt/.unos-standby/boot}
lib=${UNOS_STANDBY_LIB:-/var/lib/unos-standby}
secrets=${UNOS_STANDBY_SECRETS:-/etc/unos/secrets.env}

reason=$(sed -n 's/.*unos\.reason=\([a-z_]*\).*/\1/p' "${UNOS_STANDBY_CMDLINE:-/proc/cmdline}")
if [ ! -e "$boot/unos/emmc-failed" ]; then
    printf 'reason=%s\ntime=%s\n' "${reason:-unknown}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$boot/unos/emmc-failed.tmp"
    sync "$boot/unos/emmc-failed.tmp"
    mv -f "$boot/unos/emmc-failed.tmp" "$boot/unos/emmc-failed"
    sync "$boot/unos"
fi
if [ -e "$boot/unos/drill.txt" ]; then
    rm -f "$boot/unos/drill.txt"
    sync "$boot/unos"
fi

for archive in "$lib"/images/*.tar.gz; do
    [ -e "$archive" ] || continue
    id=$(cat "${archive%.tar.gz}.id")
    docker image inspect "$id" > /dev/null 2>&1 || docker load -i "$archive"
done

if [ -r "$secrets" ]; then
    set -a
    # shellcheck source=/dev/null
    . "$secrets"
    set +a
fi
cd "$UNOS_COMPOSE_DIR"
generation=$(cat "$lib/generation")
if [ "$(cat "$lib/seeded" 2> /dev/null || true)" != "$generation" ]; then
    docker compose -p "$UNOS_COMPOSE_PROJECT" create --no-build
    for folder in "$lib"/volumes/*/; do
        [ -d "$folder" ] || continue
        name=$(basename "$folder")
        target=$(docker volume inspect --format '{{.Mountpoint}}' "${UNOS_COMPOSE_PROJECT}_$name")
        rsync -aHAX --delete --exclude='/unos.db.sha256' "$folder" "$target/"
    done
    printf '%s\n' "$generation" > "$lib/seeded"
    sync "$lib/seeded"
fi
docker compose -p "$UNOS_COMPOSE_PROJECT" up -d --no-build
