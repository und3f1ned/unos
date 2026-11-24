"""The SD card standby's boot choice: the card's boot.scr and the small files it reads (ADR-0471).

U-Boot's standard boot scans the card before the eMMC, whichever loader the boot ROM took, and
runs ``boot.scr`` of the card's FAT partition, which the profile's partition table lets it see (GPT on
the BPI-W3, ADR-0488): the chooser. It imports ``unos/current.txt`` (``unos/previous.txt`` when that one is
missing or names no generation) and ``unos/drill.txt`` when present, reads the tries counter from the
card's raw counter sector, then takes the first case that applies. An ``env import`` that names
variables deletes those its file lacks, as U-Boot's does: the eMMC's partition is set again after the
drill's file.

1. ``unos/emmc-failed`` exists: the card's root (``unos.reason=marked``);
2. the counter holds 3: the card's root (``tries``);
3. the eMMC's own boot script loads: the counter is stepped (missing or 0 to 1, 1 to 2, 2 to 3, with
   unrolled ``test`` branches: the W3's loader has no ``setexpr``, ``itest`` or ``bootcount``) and
   written back with ``mmc write``, and the eMMC's script runs as U-Boot's standard boot would run it,
   with ``unos.chooser=card unos.root=emmc unos.reason=emmc panic=10`` (and a drill's arguments) in
   the variable that Armbian's script appends to the kernel's command line. Should that script
   return, it did not boot the eMMC: the card's root (``emmc_boot_failed``);
4. otherwise the card's root (``emmc_unreadable``): the copy of Armbian's script in the generation's
   boot folder, with ``unos.root=card`` and the generation's subvolume.

It never writes the FAT partition (``fatwrite``) nor U-Boot's environment (``saveenv``). Without any
generation it echoes and returns, and the loader's standard boot goes on to the next boot device.
"""

from __future__ import annotations

import re
import struct
import zlib

from .profiles import SECTOR, BoardProfile

# The legacy U-Boot image header (include/image.h): its magic, and the image's OS, architecture,
# type and compression as Armbian's `mkimage -C none -A arm -T script` sets them.
IMAGE_MAGIC = 0x27051956
IH_OS_LINUX = 5
IH_ARCH_ARM = 2
IH_TYPE_SCRIPT = 6
IH_COMP_NONE = 0
HEADER_BYTES = 64
IMAGE_NAME = "UNOS SD standby chooser"

# The drills (ADR-0471): a partition of the eMMC that does not exist (U-Boot reads partition numbers
# as hex: 0x7f, 127), and a root that does not exist, which the kernel takes over the eMMC's own
# since it uses the last root= of its command line.
MISSING_PARTITION = "7f"
MISSING_ROOT_UUID = "00000000-0000-4000-8000-0000756e6f73"

_PREFIX = re.compile(r"/(?:[A-Za-z0-9._-]+/)*")


def chooser_text(profile: BoardProfile, *, emmc_part: int, emmc_prefix: str) -> str:
    """The chooser for the board of ``profile``, whose eMMC holds its boot script at
    ``<emmc_prefix>boot.scr`` on partition ``emmc_part``. One statement per line."""
    if not _PREFIX.fullmatch(emmc_prefix):
        raise ValueError(f"the eMMC's boot folder {emmc_prefix!r} is no plain absolute folder ending in /")
    if not 1 <= emmc_part <= 128:
        raise ValueError(f"no partition number: {emmc_part}")
    if not re.fullmatch(r"[a-z_]+", profile.buffer_variable):
        raise ValueError(f"no U-Boot variable: {profile.buffer_variable!r}")
    card, emmc, args = profile.card_mmc, profile.emmc_mmc, profile.args_variable
    imported = "unos_gen unos_subvolid unos_bootdir"
    return f"""# UNOS: the boot choice of the SD card standby (ADR-0471), for the {profile.name}.
# UNOS writes this script when it sets up the standby; do not edit it.
setenv unos_card {card}
setenv unos_emmc {emmc}
setenv unos_emmc_part {emmc_part:x}
setenv unos_emmc_prefix {emmc_prefix}
setenv unos_counter 0x{profile.counter_sector:x}
setenv unos_buf ${{{profile.buffer_variable}}}
setenv unos_gen
setenv unos_subvolid
setenv unos_bootdir
setenv unos_drill_args
setenv unos_tries
setenv unos_reason
if load mmc ${{unos_card}}:1 ${{unos_buf}} unos/current.txt; then
env import -t ${{unos_buf}} ${{filesize}} {imported}
fi
if test -z "${{unos_gen}}"; then
if load mmc ${{unos_card}}:1 ${{unos_buf}} unos/previous.txt; then
env import -t ${{unos_buf}} ${{filesize}} {imported}
fi
fi
if load mmc ${{unos_card}}:1 ${{unos_buf}} unos/drill.txt; then
env import -t ${{unos_buf}} ${{filesize}} unos_emmc_part unos_drill_args
fi
if test -z "${{unos_emmc_part}}"; then
setenv unos_emmc_part {emmc_part:x}
fi
mmc dev ${{unos_card}}
if mmc read ${{unos_buf}} ${{unos_counter}} 1; then
env import -t ${{unos_buf}} 0x200 unos_tries
fi
if test -e mmc ${{unos_card}}:1 unos/emmc-failed; then
setenv unos_reason marked
fi
if test -z "${{unos_reason}}"; then
if test "${{unos_tries}}" = "3"; then
setenv unos_reason tries
fi
fi
if test -z "${{unos_reason}}"; then
if load mmc ${{unos_emmc}}:${{unos_emmc_part}} ${{scriptaddr}} ${{unos_emmc_prefix}}boot.scr; then
setenv unos_next 1
if test "${{unos_tries}}" = "1"; then
setenv unos_next 2
fi
if test "${{unos_tries}}" = "2"; then
setenv unos_next 3
fi
setenv unos_tries ${{unos_next}}
env export -t ${{unos_buf}} unos_tries
mmc dev ${{unos_card}}
mmc write ${{unos_buf}} ${{unos_counter}} 1
setenv unos_args "unos.chooser=card unos.root=emmc unos.reason=emmc panic=10"
if test -n "${{unos_drill_args}}"; then
setenv unos_args "${{unos_args}} ${{unos_drill_args}}"
fi
setenv devtype mmc
setenv devnum ${{unos_emmc}}
setenv distro_bootpart ${{unos_emmc_part}}
setenv prefix ${{unos_emmc_prefix}}
setenv {args} "${{unos_args}}"
source ${{scriptaddr}}
setenv unos_reason emmc_boot_failed
else
setenv unos_reason emmc_unreadable
fi
fi
if test -n "${{unos_gen}}"; then
if load mmc ${{unos_card}}:1 ${{scriptaddr}} ${{unos_bootdir}}boot.scr; then
setenv devtype mmc
setenv devnum ${{unos_card}}
setenv distro_bootpart 1
setenv prefix ${{unos_bootdir}}
setenv {args} "unos.chooser=card unos.root=card unos.reason=${{unos_reason}} rootflags=subvolid=${{unos_subvolid}}"
source ${{scriptaddr}}
fi
fi
echo "UNOS standby: no generation of the card boots, the next boot device follows"
"""


def script_image(text: str, name: str = IMAGE_NAME) -> bytes:
    """``text`` as a U-Boot script image, as ``mkimage -T script`` makes it: the legacy header (time 0,
    so that the same text makes the same bytes), then the script's length, a 0 that ends the list of
    lengths, and the script."""
    script = text.encode("utf-8")
    data = struct.pack(">II", len(script), 0) + script
    fields = (IMAGE_MAGIC, 0, 0, len(data), 0, 0, zlib.crc32(data),
              IH_OS_LINUX, IH_ARCH_ARM, IH_TYPE_SCRIPT, IH_COMP_NONE, name.encode("ascii")[:32])
    header = struct.pack(">7I4B32s", *fields)
    header = header[:4] + struct.pack(">I", zlib.crc32(header)) + header[8:]
    return header + data


def script_text(image: bytes) -> str | None:
    """The script of a U-Boot script image; None for anything else."""
    if len(image) < HEADER_BYTES + 8 or struct.unpack(">I", image[:4])[0] != IMAGE_MAGIC:
        return None
    if image[30] != IH_TYPE_SCRIPT:
        return None
    length = struct.unpack(">I", image[HEADER_BYTES:HEADER_BYTES + 4])[0]
    # The lengths end with a 0; the script follows.
    offset = HEADER_BYTES + 4
    while offset + 4 <= len(image) and struct.unpack(">I", image[offset:offset + 4])[0] != 0:
        offset += 4
    start = offset + 4
    return image[start:start + length].decode("utf-8", "replace")


def counter_block(tries: int) -> bytes:
    """The counter sector holding ``tries``: the text that U-Boot's ``env export -t`` writes, ended by a NUL,
    the rest of the sector NULs."""
    if not 0 <= tries <= 3:
        raise ValueError(f"no count of tries: {tries}")
    return f"unos_tries={tries}\n".encode().ljust(SECTOR, b"\0")


def generation_text(generation: int, subvolid: int, bootdir: str) -> str:
    """unos/current.txt (and previous.txt, next.txt): the generation, its subvolume and its boot folder."""
    return f"unos_gen={generation}\nunos_subvolid={subvolid}\nunos_bootdir={bootdir}\n"


def parse_generation(text: str) -> tuple[int, int, str] | None:
    """The generation, subvolume and boot folder of a generation_text; None when it names none."""
    values = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    try:
        return int(values["unos_gen"]), int(values["unos_subvolid"]), values["unos_bootdir"]
    except (KeyError, ValueError):
        return None


def drill_text(kind: str) -> str:
    """unos/drill.txt of a drill that the chooser acts on: an eMMC partition that does not exist, or a
    kernel root that does not exist."""
    if kind == "emmc_unreadable":
        return f"unos_emmc_part={MISSING_PARTITION}\n"
    if kind == "emmc_root_missing":
        return f"unos_drill_args=root=UUID={MISSING_ROOT_UUID}\n"
    raise ValueError(f"no drill of the chooser: {kind}")
