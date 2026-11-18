#!/usr/bin/env bash
# Read-only health check of the dev device: scripts/dev/check.sh
#
# Prints the device's system and uptime (not its host name), the device lock's holder, the
# containers (state, health, restarts), every container's log configuration (driver and
# options: the max-size and max-file that deploy/docker-compose.yml and the containers UNOS
# creates set, ADR-0330), the images, errors in the supervisor's log of the last 24
# hours, smbd, network discovery (wsdd2 and avahi-daemon; nmbd off, as NetBIOS is: ADR-0275),
# the API's health answer, the inbound IPv6 filter (ADR-0301), the system disk's writes since
# boot (and its wear data where the card reports it), free RAM, the mounted pools, each running
# container's CPU averaged over five readings of docker stats, the load average and each thermal
# zone with its first passive trip point (ADR-0374). Exits 0 when unos-supervisor and unos-webui
# run and are healthy and stay within 15% of a core, smbd, wsdd2 and avahi-daemon are active,
# nmbd neither runs nor is enabled, the API answers, the filter's unit is active with its table
# loaded and no thermal zone is within 5 C of its first passive trip (a UNOS container without a CPU
# reading is a problem too, ADR-0410); 1 otherwise ("check:
# problems").
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

# Network discovery (ADR-0275): wsdd2 for Windows, avahi-daemon for macOS. NetBIOS is off
# (disable netbios = yes), so nmbd has nothing to do and must neither run nor start at boot.
echo "== samba and network discovery"
for s in smbd wsdd2 avahi-daemon; do
    st=$(systemctl is-active "$s" 2> /dev/null) || true
    echo "$s: ${st:-unknown}"
    [ "$st" = active ] || problem "$s is ${st:-unknown}"
done
st=$(systemctl is-active nmbd 2> /dev/null) || true
en=$(systemctl is-enabled nmbd 2> /dev/null) || true
echo "nmbd: ${st:-unknown}, ${en:-unknown}"
case $st in active | activating | reloading) problem "nmbd is $st, while NetBIOS is off (ADR-0275)" ;; esac
case $en in enabled | enabled-runtime) problem "nmbd is $en, while NetBIOS is off (ADR-0275)" ;; esac

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

# Load and temperature (ADR-0374): a board that runs hot or a UNOS that keeps a core busy went
# unnoticed. Each reading of docker stats compares two samples; five readings take about ten
# seconds. A thermal zone within 5 C of its first passive trip point is about to be slowed down by
# the kernel.
echo "== load and temperature"
budget=15
readings=
for i in 1 2 3 4 5; do
    readings="$readings
$(docker stats --no-stream --format "{{.Name}} {{.CPUPerc}}" 2> /dev/null)"
    [ "$i" = 5 ] || sleep 1
done
averages=$(printf "%s\n" "$readings" | awk "NF == 2 && \$2 ~ /^[0-9.]+%\$/ { sub(/%\$/, \"\", \$2); s[\$1] += \$2; n[\$1]++ } END { for (c in s) printf \"%s %.1f\\n\", c, s[c] / n[c] }" | sort)
printf "%s\n" "$averages" | awk "NF == 2 { printf \"%s: %s%% of a core\\n\", \$1, \$2 }"
for c in unos-supervisor unos-webui; do
    avg=$(printf "%s\n" "$averages" | awk -v c="$c" "\$1 == c { print \$2 }")
    if [ -z "$avg" ]; then
        # A container that runs and has no reading must not pass for one within its budget.
        case $(docker inspect -f "{{.State.Status}}" "$c" 2> /dev/null) in
            running) problem "$c has no CPU reading from docker stats, so its budget of $budget% of a core is unchecked (ADR-0374)" ;;
        esac
        continue
    fi
    if awk -v a="$avg" -v b="$budget" "BEGIN { exit !(a > b) }"; then
        problem "$c used $avg% of a core on average, more than its budget of $budget% (ADR-0374)"
    fi
done
[ -r /proc/loadavg ] && echo "load average: $(cut -d " " -f 1-3 /proc/loadavg)"
thermal=${UNOS_CHECK_THERMAL_DIR:-/sys/class/thermal}
zones=0
for z in "$thermal"/thermal_zone*; do
    [ -r "$z/temp" ] || continue
    zones=$((zones + 1))
    name=$(cat "$z/type" 2> /dev/null) || name=${z##*/}
    raw=$(cat "$z/temp" 2> /dev/null) || raw=
    case $raw in
        "" | - | *[!0-9-]* | ?*-*)
            echo "$name: temperature unreadable"
            continue
            ;;
    esac
    t=$((raw / 1000))
    trip=
    for tt in "$z"/trip_point_*_type; do
        [ -r "$tt" ] && [ "$(cat "$tt")" = passive ] || continue
        v=$(cat "${tt%_type}_temp" 2> /dev/null) || v=
        case $v in "" | - | *[!0-9-]* | ?*-*) continue ;; esac
        v=$((v / 1000))
        if [ -z "$trip" ] || [ "$v" -lt "$trip" ]; then trip=$v; fi
    done
    if [ -z "$trip" ]; then
        echo "$name: $t C"
        continue
    fi
    echo "$name: $t C, first passive trip $trip C"
    if [ "$t" -ge $((trip - 5)) ]; then
        problem "$name is at $t C, within 5 C of its first passive trip ($trip C), where the kernel slows the CPU down: the board needs cooling or less load"
    fi
done
[ "$zones" -gt 0 ] || echo "no thermal zones"

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
