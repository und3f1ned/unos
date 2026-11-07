#!/usr/bin/env bash
# Writes to the dev device's system disk, an SD card that cannot be replaced (ADR-0214).
#
#   scripts/dev/sd.sh sectors
#       Sectors (512 bytes) written to the system disk since boot, after a sync.
#   scripts/dev/sd.sh measure <label> -- <command> [args...]
#       Run a command here (it may use ssh.sh) and print, on standard error, the MiB written to
#       the device's system disk meanwhile ("SD writes <label>: 0.055 MiB (112 sectors)").
#       Exits with the command's status. Writes by anything else on the device in that time
#       count too: compare with an idle interval when a number looks high.
#
# The system disk is UNOS_DEV_SYSTEM_DISK from dev.env, else the disk of the device's root
# filesystem. With UNOS_DEV_SD_LOG set, every measurement is also appended to that file.
set -euo pipefail
usage() { awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0"; }
case ${1:-} in
    -h | --help) usage; exit 0 ;;
    sectors | measure) ;;
    *) usage >&2; exit 2 ;;
esac
. "$(dirname "${BASH_SOURCE[0]}")/env.sh" || exit 1

case $1 in
    sectors)
        [ $# -eq 1 ] || { usage >&2; exit 2; }
        dev_sd_sectors
        ;;
    measure)
        if [ $# -lt 4 ] || [ "$3" != -- ] || [ -z "$2" ]; then
            usage >&2
            exit 2
        fi
        label=$2
        shift 3
        dev_sd_begin
        rc=0
        "$@" || rc=$?
        dev_sd_report "$label"
        exit "$rc"
        ;;
esac
