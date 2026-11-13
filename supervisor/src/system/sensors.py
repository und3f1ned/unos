"""The one temperature that the dashboard shows: the host's CPU or SoC (ADR-0346).

`/api/v1/system/stats` (`cpu_temperature`) and the stats message of `/ws/v1/events`
(`cpu.temperature`) both take it from `cpu_temperature`; `/api/v1/system/temperatures` lists every
sensor.
"""
import psutil

# The sensor groups of a CPU or SoC, as psutil names them, in order of preference, each with the
# labels of the readings that stand for the whole chip, in order of preference; a group without
# labels here, or without a reading of one of them, gives its first reading. psutil names a group
# after its hwmon device; the kernel names the hwmon device of a thermal zone after the zone, with
# "_" for "-" (soc-thermal is soc_thermal). On a host without hwmon psutil names the groups after
# the thermal zones themselves, so a name is compared with "-" read as "_".
CPU_SENSORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("coretemp", ("Package id 0",)),  # Intel
    # AMD. On the Ryzen X CPUs of the first two generations and the Threadripper 1000 and 2000,
    # Tctl is the die's temperature plus an offset of 10 to 27 degrees, and k10temp also lists
    # Tdie, the die's own. Elsewhere Tctl alone is listed, and it is the die's temperature.
    ("k10temp", ("Tdie", "Tctl")),
    ("cpu_thermal", ()),  # Raspberry Pi, Allwinner, Amlogic, RK3399 and other ARM boards
    ("soc_thermal", ()),  # RK3588 under Rockchip's kernel: the sensor of the whole chip
    ("package_thermal", ()),  # RK3588 under the mainline kernel: the same sensor
    # SpacemiT K1 (Banana Pi BPI-F3) under the vendor's drivers (Armbian's current-spacemit
    # kernel): the first of the CPU's two clusters; top_thermal and gpu_thermal are the rest of the
    # chip (ADR-0369).
    ("cluster0_thermal", ()),
)


def cpu_temperature() -> float | None:
    """The temperature of the host's CPU or SoC in degrees Celsius: the reading of the first group
    of CPU_SENSORS that psutil lists; on a host without one, the first reading of the first group
    psutil lists, which may be another chip's or a disk's. None on a host without sensors, or when
    they cannot be read (psutil has no sensors_temperatures on macOS)."""
    try:
        groups = {name.replace("-", "_"): readings
                  for name, readings in psutil.sensors_temperatures().items() if readings}
    except Exception:
        return None
    for name, labels in CPU_SENSORS:
        readings = groups.get(name)
        if readings:
            labelled = (r for label in labels for r in readings if r.label == label)
            return next(labelled, readings[0]).current
    return next((readings[0].current for readings in groups.values()), None)
