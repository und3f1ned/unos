#!/usr/bin/env bash
# The dev device's inbound IPv6 filter (ADR-0301): scripts/dev/ipv6-filter.sh install|status|remove
#
# install  Puts scripts/dev/device/ipv6-inbound.nft on the device as
#          /etc/unos-dev/ipv6-inbound.nft, with the LAN's global prefixes added to its set lan:
#          the device's on-link global routes (ip -6 -o route show, without a gateway), read
#          there, so that they stay out of the repository. Refuses when the device has global
#          addresses but no such route; without a global address it adds none, so run it once the
#          device has its global addresses. Builds the files in RAM, checks the ruleset with
#          nft -c, and writes only a file that differs from the installed one; enables
#          unos-ipv6-inbound.service (scripts/dev/device/), which loads the file at every boot,
#          and loads it now. Run it again after the LAN's prefix changed.
# status   Read-only: whether the unit is enabled and active, and the loaded table.
# remove   Disables and stops the unit (its stop deletes the table) and removes both files; fails
#          unless the unit, the files and the table are gone.
#
# install and remove change the device's security settings: they run on the owner's request
# (ADR-0024). They hold the device lock (ADR-0210) and report their SD writes (ADR-0214); status
# does neither. check.sh reports a filter that is not loaded. The filter touches IPv6 only: the
# workstation reaches the device over IPv4 throughout.
set -euo pipefail
usage() { awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0"; }
case ${1:-} in
    -h | --help) usage; exit 0 ;;
    install | status | remove) step=$1 ;;
    *) usage >&2; exit 2 ;;
esac
# One word only: "remove --help" must not remove anything.
[ $# -eq 1 ] || { usage >&2; exit 2; }
. "$(dirname "${BASH_SOURCE[0]}")/env.sh" || exit 1

# $1, $2: the ruleset and the unit, in base64. The files are built and checked in RAM; one that
# equals the installed file is not written again (ADR-0214). UNOS_DEV_FILTER_ROOT: a root
# directory for the tests, which run the device side on the workstation.
REMOTE_INSTALL='
set -euo pipefail
root=${UNOS_DEV_FILTER_ROOT-}
rules=$root/etc/unos-dev/ipv6-inbound.nft
unit=$root/etc/systemd/system/unos-ipv6-inbound.service
# The global prefixes of the LAN: the on-link global routes (no gateway), whatever set them; -o
# keeps the next hops of a multipath route on its line.
prefixes=
while read -r dst rest; do
    case $dst in [23]*:*/*) ;; *) continue ;; esac
    case " $rest " in *" via "*) continue ;; esac
    prefixes=${prefixes:+$prefixes, }$dst
done < <(ip -6 -o route show)
# Not a pipe into grep -q: under pipefail, ip ended by SIGPIPE would hide the match.
if [ -z "$prefixes" ] && [[ $(ip -6 -o addr show scope global) =~ inet6\ [23][0-9a-f]*: ]]; then
    echo "the device has global IPv6 addresses but no on-link route for the global addresses (a static or DHCPv6 address, or a prefix announced off-link?): LAN hosts that use them would be refused; nothing changed" >&2
    exit 1
fi
echo "global LAN prefixes: ${prefixes:-none}"
tmp=$(mktemp -d "$root/dev/shm/unos-ipv6-filter.XXXXXX")
trap "rm -rf \"$tmp\"" EXIT
{
    printf %s "$1" | base64 -d
    if [ -n "$prefixes" ]; then
        printf "\n# The global prefixes of the LAN: on-link routes of the device at install time.\n"
        printf "add element ip6 unos_ipv6_inbound lan { %s }\n" "$prefixes"
    fi
} > "$tmp/rules"
printf %s "$2" | base64 -d > "$tmp/unit"
if ! nft -c -f "$tmp/rules"; then
    echo "nft refused the ruleset: nothing changed" >&2
    exit 1
fi
# put FILE DEST: install FILE as DEST unless DEST holds the same bytes; 0 when DEST changed. It
# runs where set -e does not apply (in an if), so a failed write ends the script itself.
put() {
    if cmp -s "$1" "$2"; then return 1; fi
    if ! { mkdir -p "${2%/*}" && cp "$1" "$2.new" && mv -f "$2.new" "$2"; }; then
        rm -f "$2.new"
        echo "could not write $2: the loaded filter is unchanged" >&2
        exit 1
    fi
}
changed=
if put "$tmp/rules" "$rules"; then changed="$changed $rules"; fi
if put "$tmp/unit" "$unit"; then
    changed="$changed $unit"
    systemctl daemon-reload
fi
echo "written:${changed:- nothing, both files unchanged}"
systemctl is-enabled --quiet unos-ipv6-inbound.service || systemctl enable --quiet unos-ipv6-inbound.service
systemctl reload-or-restart unos-ipv6-inbound.service
echo "unos-ipv6-inbound.service: $(systemctl is-enabled unos-ipv6-inbound.service), $(systemctl is-active unos-ipv6-inbound.service)"
'

REMOTE_STATUS='
set -uo pipefail
echo "unos-ipv6-inbound.service: $(systemctl is-enabled unos-ipv6-inbound.service 2>&1), $(systemctl is-active unos-ipv6-inbound.service 2>&1)"
nft list table ip6 unos_ipv6_inbound 2> /dev/null || echo "table ip6 unos_ipv6_inbound: not loaded"
'

REMOTE_REMOVE='
set -uo pipefail
root=${UNOS_DEV_FILTER_ROOT-}
unit=$root/etc/systemd/system/unos-ipv6-inbound.service
rules=$root/etc/unos-dev/ipv6-inbound.nft
systemctl disable --now --quiet unos-ipv6-inbound.service 2> /dev/null
rm -f "$unit" "$rules"
rmdir "$root/etc/unos-dev" 2> /dev/null
systemctl daemon-reload
# A unit that failed (at boot, say) would stay listed as failed after its file is gone.
systemctl reset-failed unos-ipv6-inbound.service 2> /dev/null
nft delete table ip6 unos_ipv6_inbound 2> /dev/null
if [ -e "$unit" ] || [ -e "$rules" ] || nft list table ip6 unos_ipv6_inbound > /dev/null 2>&1; then
    echo "the inbound IPv6 filter is not removed: see the errors above" >&2
    exit 1
fi
echo "the inbound IPv6 filter is removed"
'

b64() { base64 < "$1" | tr -d '\n'; }

# --- main
rc=0
case $step in
    status) dev_remote_ro "$REMOTE_STATUS" ;;
    install)
        dev_lock "ipv6-filter.sh install"
        dev_sd_begin
        dev_remote "$REMOTE_INSTALL" "$(b64 "$UNOS_DEV_DIR/device/ipv6-inbound.nft")" \
            "$(b64 "$UNOS_DEV_DIR/device/unos-ipv6-inbound.service")" || rc=$?
        dev_sd_report "ipv6-filter install"
        ;;
    remove)
        dev_lock "ipv6-filter.sh remove"
        dev_sd_begin
        dev_remote "$REMOTE_REMOVE" || rc=$?
        dev_sd_report "ipv6-filter remove"
        ;;
esac
exit "$rc"
