#!/usr/bin/env bash
# Scenario checks of the dev device (ADR-0304): scripts/dev/scenario.sh [--pool NAME] AREA...
#
#   AREA     files, volumes, smart, backups, smb or smbconf; all: every area, in this order.
#   --pool   the pool of the files, volumes, smb and smbconf checks (default: the one pool that is
#            mounted read-write with UNOS's fstab entry).
#
# Each check runs the user scenario of its area on the device through the API and on the host
# (scripts/dev/scenario.py --help says what each covers). It makes temporary things (a user, a
# share, a volume and folders whose names start with "unoscheck", and two backups; smbconf also
# edits smb.conf by hand and restarts the supervisor, ADR-0309), prints PASS or
# FAIL for every step with the MiB written to the SD card meanwhile, removes what it made, also
# after a FAIL or Ctrl-C, and compares the device with how it found it. The checks change the device:
# run them when the owner asks for a check of the device (ADR-0016), for example the re-check
# after a reboot (ADR-0018). The feature-pass skill names the check of each host-level entry.
#
# Holds the device lock for the whole run (ADR-0210) and reports the SD writes of each check
# (ADR-0214). Exit status: 0 when every check passed, 1 when one failed, 2 for a usage error.
# Settings: dev.env (see env.sh); UNOS_DEV_PYTHON (python3) runs scenario.py.
set -euo pipefail
usage() { awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0"; }
AREAS=(files volumes smart backups smb smbconf)
case ${1:-} in
    -h | --help) usage; exit 0 ;;
esac
pool=
areas=()
while [ $# -gt 0 ]; do
    case $1 in
        --pool)
            [ $# -ge 2 ] && [[ $2 =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ ]] || { usage >&2; exit 2; }
            pool=$2
            shift 2
            ;;
        all) areas+=("${AREAS[@]}"); shift ;;
        files | volumes | smart | backups | smb | smbconf) areas+=("$1"); shift ;;
        *) usage >&2; exit 2 ;;
    esac
done
[ ${#areas[@]} -gt 0 ] || { usage >&2; exit 2; }
. "$(dirname "${BASH_SOURCE[0]}")/env.sh" || exit 1

PYTHON3=${UNOS_DEV_PYTHON:-python3}

# --- main
dev_lock "scenario.sh ${areas[*]}"
# A signal reaches scenario.py too, which then removes what its check made: the run waits for
# it (bash runs a trap only after the command it waits for), reports that check's SD writes,
# and ends. The device lock is released by the exit handler of env.sh.
stop=
trap 'stop=130' INT
trap 'stop=143' TERM
trap 'stop=129' HUP
failed=
for area in "${areas[@]}"; do
    start=$(dev_sd_sectors) || start=
    # A signal during the measurement ends that ssh only: no new check starts after it.
    [ -z "$stop" ] || break
    rc=0
    "$PYTHON3" "$UNOS_DEV_DIR/scenario.py" "$area" ${pool:+--pool "$pool"} || rc=$?
    dev_sd_report_from "$start" "scenario $area"
    [ "$rc" -eq 0 ] || failed="$failed $area"
    # An interrupted check (130) ends the run.
    if [ "$rc" -eq 130 ] && [ -z "$stop" ]; then stop=130; fi
    [ -z "$stop" ] || break
done
if [ -n "$failed" ]; then
    dev_warn "failed:$failed"
fi
[ -z "$stop" ] || exit "$stop"
[ -z "$failed" ] || exit 1
