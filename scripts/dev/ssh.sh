#!/usr/bin/env bash
# SSH to the dev device as UNOS_DEV_SSH_USER@UNOS_DEV_HOST (settings: dev.env, see env.sh).
#
#   scripts/dev/ssh.sh 'docker ps'          run a command on the device (one string, as for ssh)
#   scripts/dev/ssh.sh 'cat > /dev/shm/x' < file     standard input passes through
#   scripts/dev/ssh.sh                      an interactive shell (from a terminal)
#
# Authentication: SSH keys first (ADR-0212: keys are preferred). With UNOS_DEV_SSH_PASSWORD
# set in dev.env, ssh then asks scripts/dev/askpass.sh for the password (SSH_ASKPASS): no
# password on a command line, in the environment or in a pty. With it empty, keys only.
# Optional settings: UNOS_DEV_SSH_KEY (a private key file to use, alone), UNOS_DEV_KNOWN_HOSTS
# (a known_hosts file instead of ~/.ssh/known_hosts). The host key is checked: a new host is
# added once, a changed key is refused (run `ssh-keygen -R <host>` after reinstalling it).
#
# On the device, read with care: commands that write files, refresh git's index (git status),
# create containers or pull images write to its SD card (ADR-0214); scripts/dev/sd.sh
# measures how much.
set -euo pipefail
case ${1:-} in
    -h | --help) awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0"; exit 0 ;;
esac
. "$(dirname "${BASH_SOURCE[0]}")/env.sh" || exit 1

opts=(-o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -o LogLevel=ERROR
      -o StrictHostKeyChecking=accept-new)
if [ -n "${UNOS_DEV_KNOWN_HOSTS:-}" ]; then
    opts+=(-o "UserKnownHostsFile=$UNOS_DEV_KNOWN_HOSTS")
fi
if [ -n "${UNOS_DEV_SSH_KEY:-}" ]; then
    opts+=(-i "$UNOS_DEV_SSH_KEY" -o IdentitiesOnly=yes)
fi
if dev_has_secret UNOS_DEV_SSH_PASSWORD; then
    SSH_ASKPASS="$UNOS_DEV_DIR/askpass.sh"
    SSH_ASKPASS_REQUIRE=force
    export SSH_ASKPASS SSH_ASKPASS_REQUIRE
    # shellcheck disable=SC2054 # the comma separates ssh's methods
    opts+=(-o PreferredAuthentications=publickey,password -o NumberOfPasswordPrompts=1
           -o KbdInteractiveAuthentication=no)
else
    opts+=(-o BatchMode=yes)
fi
if [ $# -eq 0 ] && [ -t 0 ]; then
    opts+=(-t)
fi
exec ssh "${opts[@]}" -- "$UNOS_DEV_SSH_USER@$UNOS_DEV_HOST" "$@"
