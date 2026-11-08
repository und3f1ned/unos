#!/usr/bin/env bash
# Read-only health check of the dev device: scripts/dev/check.sh
#
# Prints the device's system and uptime (not its host name), the device lock's holder, the
# containers (state, health, restarts), every container's log configuration (driver and
# options, such as the max-size and max-file of a rotation set in the host's
# /etc/docker/daemon.json, ADR-0178), the images, errors in the supervisor's log of the last 24
# hours, smbd (and nmbd where NetBIOS is on), the API's health answer, the inbound IPv6 filter
# (ADR-0301), the system disk's writes since boot (and its wear data where the card reports it),
# free RAM and the mounted pools. Exits 0 when unos-supervisor and unos-webui run and are
# healthy, smbd is active (nmbd too, unless smb.conf turns NetBIOS off: ADR-0275), the API
# answers and the filter's unit is active with its table loaded; 1 otherwise ("check: problems").
#
# It runs one script on the device, with the root filesystem read-only (dev_remote_ro of
# env.sh, ADR-0221), starts no container (ADR-0183), takes no lock (it waits for nobody) and
# reports the SD writes of the check itself (ADR-0214). The output passes through the redaction
# of api.py, in case a log line carries a secret.
set -euo pipefail
case ${1:-} in
    -h | --help) awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0"; exit 0 ;;
    '') ;;
    *) awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0" >&2; exit 2 ;;
esac
. "$(dirname "${BASH_SOURCE[0]}")/env.sh" || exit 1

REMOTE_CHECK='
set -uo pipefail
disk=$1 lock=$2
problems=
problem() { problems="$problems
  - $*"; }

echo "== device"
echo "$(. /etc/os-release && echo "$PRETTY_NAME"), kernel $(uname -r), $(uptime -p)"

echo "== device lock $lock"
if (exec 9>> "$lock" && flock -n 9); then
    echo "free"
else
    echo "held by $(cat "$lock.owner" 2> /dev/null || echo "an unknown holder")"
fi

echo "== containers"
docker ps -a --format "{{.Names}}  {{.Status}}  {{.Image}}" | sort
for c in unos-supervisor unos-webui; do
    s=$(docker inspect -f "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}} restarts={{.RestartCount}}" "$c" 2> /dev/null) || s=
    if [ -z "$s" ]; then
        problem "$c does not exist"
        continue
    fi
    echo "$c: $s"
    case $s in
        "running healthy "*) ;;
        *) problem "$c: $s" ;;
    esac
done

echo "== container logs: driver and options (json-file without max-size: never rotated)"
ids=$(docker ps -aq)
if [ -n "$ids" ]; then
    docker inspect -f "{{.Name}}: {{.HostConfig.LogConfig.Type}} {{json .HostConfig.LogConfig.Config}}" $ids | sed "s,^/,," | sort
else
    echo "no containers"
fi

echo "== images"
docker images --format "{{.Repository}}:{{.Tag}}  {{.ID}}  {{.CreatedSince}}  {{.Size}}" | sort
echo "dangling images: $(docker images -q -f dangling=true | wc -l)"

echo "== supervisor log, last 24 hours"
log=$(docker logs --since 24h unos-supervisor 2>&1) || log=
errors=$(printf "%s\n" "$log" | grep -E "ERROR|CRITICAL|Traceback" || true)
if [ -n "$errors" ]; then
    echo "$(printf "%s\n" "$errors" | wc -l) error lines; the last ones:"
    printf "%s\n" "$errors" | tail -n 10 | cut -c 1-300
else
    echo "no ERROR, CRITICAL or Traceback lines"
fi

echo "== samba"
services=smbd
# With NetBIOS off (disable netbios = yes, ADR-0275) nmbd has nothing to do and does not run.
if grep -Eiq "^[[:space:]]*disable netbios[[:space:]]*=[[:space:]]*yes" /etc/samba/smb.conf 2> /dev/null; then
    echo "nmbd: not needed (NetBIOS is off)"
else
    services="smbd nmbd"
fi
for s in $services; do
    st=$(systemctl is-active "$s" 2> /dev/null) || true
    echo "$s: ${st:-unknown}"
    [ "$st" = active ] || problem "$s is ${st:-unknown}"
done

echo "== API"
code=$(python3 -I -c "import ssl, urllib.request as u; print(u.urlopen(\"https://127.0.0.1/api/v1/health\", context=ssl._create_unverified_context(), timeout=5).status)" 2> /dev/null) || code=none
echo "https://127.0.0.1/api/v1/health: $code"
[ "$code" = 200 ] || problem "the API health check answers $code"

# The filter fails open (ADR-0301): without its table every service answers over IPv6.
echo "== inbound IPv6 filter"
unit=$(systemctl is-active unos-ipv6-inbound.service 2> /dev/null) || true
if nft list table ip6 unos_ipv6_inbound > /dev/null 2>&1; then table=loaded; else table=missing; fi
echo "unos-ipv6-inbound.service: ${unit:-unknown}, table ip6 unos_ipv6_inbound: $table"
[ "$unit" = active ] && [ "$table" = loaded ] || problem "the inbound IPv6 filter is not loaded (scripts/dev/ipv6-filter.sh status)"

echo "== system disk"
d=$disk
[ -n "$d" ] || d=$(lsblk -no PKNAME "$(findmnt -no SOURCE /)")
read -r up _ < /proc/uptime || up=0
sectors=$(awk "{print \$7}" "/sys/block/$d/stat")
type=$(cat "/sys/block/$d/device/type" 2> /dev/null || echo unknown)
awk -v d="$d" -v t="$type" -v s="$sectors" -v u="$up" "BEGIN { printf \"%s (%s): %.1f MiB written since boot, %.1f hours ago: %.2f MiB per hour\n\", d, t, s * 512 / 1048576, u / 3600, (u > 0 ? s * 512 / 1048576 / (u / 3600) : 0) }"
if [ -r "/sys/block/$d/device/life_time" ]; then
    echo "wear estimate (life_time, 0x01 = up to 10% used): $(cat "/sys/block/$d/device/life_time"), pre_eol_info: $(cat "/sys/block/$d/device/pre_eol_info" 2> /dev/null)"
else
    echo "the card reports no wear estimate"
fi
echo "root filesystem (as the system mounts it): $(findmnt -N 1 -no SOURCE,FSTYPE,OPTIONS /)"

echo "== memory"
free -m | awk "NR <= 2"
df -m /dev/shm /run | awk "NR == 1 || /^tmpfs/"

echo "== pools"
pools=$(findmnt -rn -o TARGET,SOURCE,FSTYPE | awk "\$1 ~ /^\/mnt\/pools\//")
if [ -n "$pools" ]; then
    printf "%s\n" "$pools"
    df -h $(printf "%s\n" "$pools" | awk "{print \$1}") | tail -n +2
else
    echo "no pool mounted under /mnt/pools"
fi

echo "== result"
if [ -z "$problems" ]; then
    echo "check: OK"
else
    echo "check: problems$problems"
    exit 1
fi'

# --- main
PYTHON3=${UNOS_DEV_PYTHON:-python3}
start=$(dev_sd_sectors) || start=
rc=0
dev_remote_ro "$REMOTE_CHECK" "$UNOS_DEV_SYSTEM_DISK" "$UNOS_DEV_LOCK" | "$PYTHON3" "$UNOS_DEV_DIR/api.py" --redact || rc=$?
dev_sd_report_from "$start" "check.sh"
exit "$rc"
