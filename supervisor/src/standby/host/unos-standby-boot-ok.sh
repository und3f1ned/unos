#!/bin/sh
# UNOS: the eMMC's system came up through the SD card standby's chooser (ADR-0471). Run by
# unos-standby-boot-ok.service once the system reached multi-user, when the kernel's command line has
# unos.root=emmc: it sets the card's tries counter back to 0, one write to the card per boot.
# Argument: the counter's sector. UNOS writes this file when it sets up the standby; do not edit it.
set -eu

sector=$1
part=$(readlink -f /dev/disk/by-label/UNOS-BOOT)
disk=/dev/$(lsblk -n -d -o PKNAME "$part" | tr -d ' ')
case "$disk" in
    /dev/mmcblk[0-9]*) ;;
    *) echo "unos-standby-boot-ok: the standby's card is not an mmc device: $disk" >&2; exit 1 ;;
esac
# The counter's text as U-Boot's env export -t writes it; conv=sync fills the sector with NULs.
printf 'unos_tries=0\n' | dd of="$disk" bs=512 seek="$sector" count=1 conv=sync,notrunc,fsync status=none
