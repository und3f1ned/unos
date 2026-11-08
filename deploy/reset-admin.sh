#!/bin/bash
#
# UNOS emergency reset: a new password for an administrator, and two-factor
# authentication off, for when no administrator can sign in (docs/RECOVERY.md,
# ADR-0284).
#
#   sudo /opt/unos/deploy/reset-admin.sh [NAME]
#
# Runs `python -m src.emergency reset-admin` (supervisor/src/emergency.py) in
# the running unos-supervisor container. NAME may be left out when UNOS has one
# administrator. On a terminal the new password is asked twice and not shown;
# otherwise it is the first line of standard input. Never pass it as an
# argument: every process of the host can read a command line.
#

set -e

# container_name of the supervisor in docker-compose.yml.
CONTAINER=unos-supervisor

check_root() {
    if [ "$EUID" -ne 0 ]; then
        echo "[ERROR] This script must be run as root" >&2
        echo "Please run: sudo $0 [NAME]" >&2
        exit 1
    fi
}

main() {
    check_root
    # A terminal in the container only with one here: docker refuses -t when
    # standard input is not a terminal.
    if [ -t 0 ]; then
        exec docker exec -it "$CONTAINER" python -m src.emergency reset-admin "$@"
    fi
    exec docker exec -i "$CONTAINER" python -m src.emergency reset-admin "$@"
}

main "$@"
