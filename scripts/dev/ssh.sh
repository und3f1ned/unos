#!/usr/bin/env bash
# SSH to the dev device as UNOS_DEV_SSH_USER@UNOS_DEV_HOST (settings: dev.env, see env.sh).
#
#   scripts/dev/ssh.sh 'docker ps'          run a command on the device (one string, as for ssh)
#   scripts/dev/ssh.sh 'cat > /dev/shm/x' < file     standard input passes through
#   scripts/dev/ssh.sh                      an interactive shell (from a terminal)
#   scripts/dev/ssh.sh --close              close the shared connection (before a reboot)
#
# Authentication: SSH keys first (ADR-0212: keys are preferred). With UNOS_DEV_SSH_PASSWORD
# set in dev.env, ssh then asks scripts/dev/askpass.sh for the password (SSH_ASKPASS): no
# password on a command line, in the environment or in a pty. With it empty, keys only.
# Optional settings: UNOS_DEV_SSH_KEY (a private key file to use, alone), UNOS_DEV_KNOWN_HOSTS
# (a known_hosts file instead of ~/.ssh/known_hosts). The host key is checked: a new host is
# added once, a changed key is refused (run `ssh-keygen -R <host>` after reinstalling it).
#
# One connection per device (ADR-0477): the first call opens a master connection, and the calls
# after it run their commands over it (OpenSSH's ControlMaster), without a key exchange, login and
# PAM session each: on the boards, pam_motd runs Armbian's login scripts at every login, whatever
# the command. The master ends 300 s after its last session ends. Its socket is
# $TMPDIR/unos-ssh-<uid>/<user>@<host> (/tmp without TMPDIR), in a folder of this user's with mode
# 700. A folder that is not that, or a socket path that is not absolute, has other characters than
# letters, digits and ._/@:- or is longer than 86, makes every call connect on its own, with a
# warning. ssh replaces a master that has died, and a master that cannot open a session lets the
# call connect on its own. Over a master, a call proves no new login: it uses the connection made
# before (which a firewall lets through as established). --close stops the master taking calls
# (ssh -O stop): it ends when the commands running over it end, and the next call logs in anew.
# Use it before a check that the device takes a new login, after a change of the device's network
# or firewall, before the device reboots and before a change of dev.env's SSH settings.
#
# On the device, read with care: commands that write files, refresh git's index (git status),
# create containers or pull images write to its system disk (ADR-0214); scripts/dev/sd.sh
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
dest=$UNOS_DEV_SSH_USER@$UNOS_DEV_HOST

# The shared connection's socket (ADR-0477). sun_path holds 104 bytes on macOS, and ssh first
# binds the socket under its name and 17 more characters: at most 86 here. ssh expands % and
# splits at spaces in ControlPath: plain characters only. Checked in the C locale, where letters
# are ASCII ones and the length counts bytes.
mux_dir=${TMPDIR:-/tmp}
mux_dir=${mux_dir%/}/unos-ssh-$UID
mux_path=$mux_dir/$dest
mux_problem=$(
    LC_ALL=C
    re='^/[A-Za-z0-9._/@:-]+$'
    if [[ ! $mux_path =~ $re ]]; then
        echo "is not absolute or has other characters than letters, digits and ._/@:-"
    elif [ ${#mux_path} -gt 86 ]; then
        echo "is longer than 86 characters"
    fi
)
if [ -n "$mux_problem" ]; then
    dev_warn "not sharing the SSH connection: its socket path $mux_path $mux_problem (set TMPDIR to a short, plain folder)"
    mux_path=
else
    mkdir -m 700 "$mux_dir" 2>/dev/null || :
    if [ -L "$mux_dir" ] || [ ! -d "$mux_dir" ] || [ ! -O "$mux_dir" ] || [ "$(dev__mode "$mux_dir")" != 700 ]; then
        dev_warn "not sharing the SSH connection: $mux_dir is not a folder of $(id -un) with mode 700"
        mux_path=
    fi
fi
if [ -n "$mux_path" ]; then
    opts+=(-o ControlMaster=auto -o "ControlPath=$mux_path" -o ControlPersist=300)
fi

if [ "${1:-}" = --close ]; then
    if [ $# -ne 1 ]; then
        dev_warn "--close takes no command"
        exit 2
    fi
    # A master that died leaves its socket, which the next call's ssh removes (ADR-0477).
    if [ -n "$mux_path" ] && [ -S "$mux_path" ]; then
        ssh "${opts[@]}" -O stop -- "$dest" 2>/dev/null || :
    fi
    exit 0
fi
if [ $# -eq 0 ] && [ -t 0 ]; then
    opts+=(-t)
fi
exec ssh "${opts[@]}" -- "$dest" "$@"
