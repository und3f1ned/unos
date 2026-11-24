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

On a board whose eMMC boots through extlinux (the BPI-F3, ADR-0489), case 3 loads the kernel, initrd
and device tree of the eMMC's extlinux label itself and boots them with ``booti``, its append line and
UNOS's arguments in ``bootargs``: its U-Boot cannot pass them through ``sysboot``, which expands an
append line into 255 characters at most (CONFIG_SYS_CBSIZE=256), and the eMMC's line has 223. Case 4
loads the files that the boot folder's ``boot.txt`` names, with the card's command line.

It never writes the FAT partition (``fatwrite``) nor U-Boot's environment (``saveenv``). Without any
generation it echoes and returns, and the loader's standard boot goes on to the next boot device.
"""

from __future__ import annotations

import re
import struct
import zlib
from dataclasses import dataclass

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


# What may stand in an extlinux append line that the chooser copies into a quoted setenv of U-Boot's
# shell: letters, digits, spaces and _.,:=/+@%~-, so no quote, $, backslash, ; or # (ADR-0489).
_APPEND = re.compile(r"[A-Za-z0-9 _.,:=/+@%~-]*")
_BOOT_PATH = re.compile(r"/(?:[A-Za-z0-9_+-][A-Za-z0-9._+-]*/)*[A-Za-z0-9_+-][A-Za-z0-9._+-]*")
# extlinux's keywords for a label's files and command line, and the synonyms U-Boot's pxe takes.
_LABEL_KEYS = {"kernel": "kernel", "linux": "kernel", "initrd": "initrd", "fdt": "fdt", "devicetree": "fdt",
               "append": "append"}
# Global keywords that change nothing of what sysboot boots without a console; ontimeout, which names
# another label, is refused.
_GLOBAL_KEYS = {"timeout", "prompt", "say", "menu"}


@dataclass(frozen=True)
class ExtlinuxLabel:
    """The label of an extlinux.conf that U-Boot's sysboot boots: its paths on the boot partition."""

    name: str
    kernel: str
    initrd: str
    fdt: str
    append: str


def parse_extlinux(text: str) -> ExtlinuxLabel:
    """The label that sysboot boots, the one ``default`` names, else the first ``menu default`` one, else
    the first, with its kernel, initrd, device tree and append line. ValueError, with the reason, for a
    file in which the chooser could pick another label or other files than U-Boot's pxe parser: a keyword
    that the chooser does not load (fdtdir, fdtoverlays and the like), ``ontimeout`` or an ``include``, a
    global keyword after the first label (pxe ends the label there), a keyword twice in the label (pxe
    keeps the first), an ``initrd=`` in the append line, a file missing or not named by an absolute path,
    or an append line with another sign than letters, digits, spaces and ``_.,:=/+@%~-`` (ADR-0489)."""
    labels: dict[str, dict[str, str]] = {}
    order: list[str] = []
    default: str | None = None
    marked: str | None = None
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # pxe lowercases a keyword before it compares it; a value keeps its case.
        keyword, _, value = line.partition(" ")
        keyword, value = keyword.lower(), value.strip()
        menu = value.split(None, 1)[0].lower() if keyword == "menu" and value else ""
        if keyword == "ontimeout":
            raise ValueError("it names an ontimeout label, which sysboot could boot instead")
        if keyword == "include" or menu == "include":
            raise ValueError("it includes another file, which could name another default label")
        if keyword == "label":
            if not value or value in labels:
                raise ValueError(f"a label without a name or named twice: {value!r}")
            current = labels[value] = {}
            order.append(value)
        elif current is not None and keyword == "menu" and menu in ("default", "label"):
            if menu == "default" and marked is None:
                marked = order[-1]
        elif keyword == "default" and current is None:
            default = value
        elif keyword in _GLOBAL_KEYS and current is None:
            continue
        elif keyword in _GLOBAL_KEYS or keyword == "default":
            raise ValueError(f"the global keyword {keyword} after its first label")
        elif current is not None and keyword in _LABEL_KEYS:
            if _LABEL_KEYS[keyword] in current:
                raise ValueError(f"its label {order[-1]} names its {_LABEL_KEYS[keyword]} twice")
            current[_LABEL_KEYS[keyword]] = value
        elif current is not None:
            current.setdefault("unsupported", keyword)
        else:
            raise ValueError(f"the keyword {keyword} outside a label")
    if not order:
        raise ValueError("it has no label")
    name = default or marked or order[0]
    if name not in labels:
        raise ValueError(f"its default label {name} does not exist")
    label = labels[name]
    if "unsupported" in label:
        raise ValueError(f"its label {name} uses {label['unsupported']}, which the chooser does not load")
    for key in ("kernel", "initrd", "fdt", "append"):
        if not label.get(key):
            raise ValueError(f"its label {name} has no {key}")
    for key in ("kernel", "initrd", "fdt"):
        if not _BOOT_PATH.fullmatch(label[key]):
            raise ValueError(f"its label {name} names its {key} {label[key]!r}, not a plain absolute path")
    if not _APPEND.fullmatch(label["append"]):
        raise ValueError(f"the append line of its label {name} holds a quote, $, backslash, ; or another sign "
                         "that the chooser cannot copy")
    if any(word.startswith("initrd=") for word in label["append"].split()):
        raise ValueError(f"the append line of its label {name} names an initrd=, which pxe would load")
    return ExtlinuxLabel(name=name, kernel=label["kernel"], initrd=label["initrd"], fdt=label["fdt"],
                         append=" ".join(label["append"].split()))


def chooser_text(profile: BoardProfile, *, emmc_part: int, emmc_prefix: str,
                 emmc_label: ExtlinuxLabel | None = None) -> str:
    """The chooser for the board of ``profile``, whose eMMC boots from partition ``emmc_part`` through
    ``<emmc_prefix>boot.scr`` or, for an extlinux board, through ``emmc_label``. One statement per line."""
    if not _PREFIX.fullmatch(emmc_prefix):
        raise ValueError(f"the eMMC's boot folder {emmc_prefix!r} is no plain absolute folder ending in /")
    if not 1 <= emmc_part <= 128:
        raise ValueError(f"no partition number: {emmc_part}")
    if not re.fullmatch(r"[a-z_]+", profile.buffer_variable):
        raise ValueError(f"no U-Boot variable: {profile.buffer_variable!r}")
    if profile.emmc_boot == "extlinux":
        if emmc_label is None:
            raise ValueError(f"the {profile.name}'s chooser needs the eMMC's extlinux label")
        handover, card_root = _extlinux_handover(emmc_label), _EXTLINUX_CARD
    else:
        if not re.fullmatch(r"[a-z_]+", profile.args_variable):
            raise ValueError(f"no U-Boot variable: {profile.args_variable!r}")
        handover, card_root = _boot_scr_handover(profile.args_variable), _boot_scr_card(profile.args_variable)
    return _prelude(profile, emmc_part, emmc_prefix) + handover + card_root + _NO_GENERATION


def _prelude(profile: BoardProfile, emmc_part: int, emmc_prefix: str) -> str:
    """The variables, the generation, a drill, the counter and the first two cases: the mark and 3 tries."""
    imported = "unos_gen unos_subvolid unos_bootdir"
    return f"""# UNOS: the boot choice of the SD card standby (ADR-0471), for the {profile.name}.
# UNOS writes this script when it sets up the standby; do not edit it.
setenv unos_card {profile.card_mmc}
setenv unos_emmc {profile.emmc_mmc}
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
"""


# One try counted: the counter stepped with unrolled test branches and written back to its sector.
_COUNT = """setenv unos_next 1
if test "${unos_tries}" = "1"; then
setenv unos_next 2
fi
if test "${unos_tries}" = "2"; then
setenv unos_next 3
fi
setenv unos_tries ${unos_next}
env export -t ${unos_buf} unos_tries
mmc dev ${unos_card}
mmc write ${unos_buf} ${unos_counter} 1
"""
_EMMC_ARGS = "unos.chooser=card unos.root=emmc unos.reason=emmc panic=10"
_CARD_ARGS = "unos.chooser=card unos.root=card unos.reason=${unos_reason} rootflags=subvolid=${unos_subvolid}"
_NO_GENERATION = 'echo "UNOS standby: no generation of the card boots, the next boot device follows"\n'


def _boot_scr_handover(args: str) -> str:
    """Case 3 for Armbian's boot.scr: its script loads and runs as U-Boot's standard boot would run it."""
    return f"""if test -z "${{unos_reason}}"; then
if load mmc ${{unos_emmc}}:${{unos_emmc_part}} ${{scriptaddr}} ${{unos_emmc_prefix}}boot.scr; then
{_COUNT}setenv unos_args "{_EMMC_ARGS}"
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
"""


def _boot_scr_card(args: str) -> str:
    """Case 4 for Armbian's boot.scr: its copy in the generation's boot folder, with the card's arguments."""
    return f"""if test -n "${{unos_gen}}"; then
if load mmc ${{unos_card}}:1 ${{scriptaddr}} ${{unos_bootdir}}boot.scr; then
setenv devtype mmc
setenv devnum ${{unos_card}}
setenv distro_bootpart 1
setenv prefix ${{unos_bootdir}}
setenv {args} "{_CARD_ARGS}"
source ${{scriptaddr}}
fi
fi
"""


def _extlinux_handover(label: ExtlinuxLabel) -> str:
    """Case 3 for extlinux: the label's files from the eMMC and booti, as sysboot boots them (the initrd
    as address:size), with the label's append line and UNOS's arguments (ADR-0489)."""
    if not _APPEND.fullmatch(label.append) or not all(
            _BOOT_PATH.fullmatch(path) for path in (label.kernel, label.initrd, label.fdt)):
        raise ValueError(f"the extlinux label {label.name} cannot be copied into the chooser")
    emmc = "mmc ${unos_emmc}:${unos_emmc_part}"
    return f"""if test -z "${{unos_reason}}"; then
setenv unos_loaded
if load {emmc} ${{kernel_addr_r}} {label.kernel}; then
if load {emmc} ${{ramdisk_addr_r}} {label.initrd}; then
setenv unos_initrd_size ${{filesize}}
if load {emmc} ${{fdt_addr_r}} {label.fdt}; then
setenv unos_loaded 1
fi
fi
fi
if test "${{unos_loaded}}" = "1"; then
{_COUNT}setenv bootargs "{label.append} {_EMMC_ARGS}"
if test -n "${{unos_drill_args}}"; then
setenv bootargs "${{bootargs}} ${{unos_drill_args}}"
fi
booti ${{kernel_addr_r}} ${{ramdisk_addr_r}}:${{unos_initrd_size}} ${{fdt_addr_r}}
setenv unos_reason emmc_boot_failed
else
setenv unos_reason emmc_unreadable
fi
fi
"""


# Case 4 for extlinux: the files that the boot folder's boot.txt names, with its command line, which names
# the card's btrfs, and the card's arguments.
_EXTLINUX_CARD = f"""if test -n "${{unos_gen}}"; then
setenv unos_kernel
setenv unos_initrd
setenv unos_fdt
setenv unos_cmdline
if load mmc ${{unos_card}}:1 ${{unos_buf}} ${{unos_bootdir}}boot.txt; then
env import -t ${{unos_buf}} ${{filesize}} unos_kernel unos_initrd unos_fdt unos_cmdline
fi
if test -n "${{unos_cmdline}}"; then
if load mmc ${{unos_card}}:1 ${{kernel_addr_r}} ${{unos_bootdir}}${{unos_kernel}}; then
if load mmc ${{unos_card}}:1 ${{ramdisk_addr_r}} ${{unos_bootdir}}${{unos_initrd}}; then
setenv unos_initrd_size ${{filesize}}
if load mmc ${{unos_card}}:1 ${{fdt_addr_r}} ${{unos_bootdir}}${{unos_fdt}}; then
setenv bootargs "${{unos_cmdline}} {_CARD_ARGS}"
booti ${{kernel_addr_r}} ${{ramdisk_addr_r}}:${{unos_initrd_size}} ${{fdt_addr_r}}
fi
fi
fi
fi
fi
"""


def boot_text(kernel: str, initrd: str, fdt: str, cmdline: str) -> str:
    """boot.txt of an extlinux board's boot folder: its files, relative to the folder, and the card's
    command line, as the chooser imports them."""
    for path in (kernel, initrd, fdt):
        if not _BOOT_PATH.fullmatch("/" + path):
            raise ValueError(f"no plain relative path: {path!r}")
    if not _APPEND.fullmatch(cmdline):
        raise ValueError("the card's command line holds a sign that the chooser cannot import")
    return f"unos_kernel={kernel}\nunos_initrd={initrd}\nunos_fdt={fdt}\nunos_cmdline={cmdline}\n"


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
