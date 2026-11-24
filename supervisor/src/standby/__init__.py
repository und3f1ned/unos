"""The SD card standby of an eMMC system disk (ADR-0471, phase 2: system.sd-standby-boot).

- ``profiles``: the boards it is offered on, by Armbian's BOARD (the BPI-W3's and the BPI-F3's, at the
  owner's word).
- ``bootscript``: the card's boot.scr, the chooser between the eMMC and the card, and its texts; the
  eMMC's extlinux label that the BPI-F3's chooser boots itself (ADR-0489).
- ``hostops``: every host action of the standby, through ``src/host.py``, with its dry run.
- ``conditions``: where it is offered, each condition with its result.
- ``card``: Set up the standby and Remove the standby.
- ``generation``: the sync, a new btrfs generation of the system's copy made current once complete.
- ``switch``: the switch to the card when the eMMC fails, without a read of the eMMC.
- ``service``: its state, the schedule of the syncs, its problems, the drills and the return.
- ``router``: ``/api/v1/system/standby``, for administrators only.
"""
