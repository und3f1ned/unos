#!/usr/bin/env bash
# SSH_ASKPASS helper of scripts/dev/ssh.sh: answers ssh's password prompt for the dev device
# ("<UNOS_DEV_SSH_USER>@<UNOS_DEV_HOST>'s password:") with UNOS_DEV_SSH_PASSWORD from dev.env,
# on standard output, which only ssh reads. Any other prompt (a key's passphrase, a host key
# question, another host's password) is refused with status 1 and nothing on standard output.
# With UNOS_DEV_HOST a ~/.ssh/config alias, ssh names the real host in its prompt and the
# password is refused: use the address, or an SSH key.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/env.sh" || exit 1

prompt=${1:-}
prompt=${prompt%"${prompt##*[![:space:]]}"}
if [ "$prompt" != "$UNOS_DEV_SSH_USER@$UNOS_DEV_HOST's password:" ]; then
    dev_warn "not answering the ssh prompt '$prompt': only $UNOS_DEV_SSH_USER@$UNOS_DEV_HOST's password is known here"
    exit 1
fi
dev_secret UNOS_DEV_SSH_PASSWORD
