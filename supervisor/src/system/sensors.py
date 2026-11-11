"""The one temperature that the dashboard shows: the host's CPU or SoC (ADR-0342).

`/api/v1/system/stats` (`cpu_temperature`) and the stats message of `/ws/v1/events`
(`cpu.temperature`) both take it from `cpu_temperature`; `/api/v1/system/temperatures` lists every
sensor.
"""
import psutil

# The sensor groups of a CPU or SoC, as psutil names them, in order of preference, each with the
# label of the reading that stands for the whole chip (None: the group's first reading; so is a
# group without that label). psutil names a group after its hwmon device; the kernel names the
# hwmon device of a thermal zone after the zone, with "_" for "-" (soc-thermal is soc_thermal).
# On a host without hwmon psutil names the groups after the thermal zones themselves, so a name is
# compared with "-" read as "_".
CPU_SENSORS: tuple[tuple[str, str | None], ...] = (
    ("coretemp", "Package id 0"),  # Intel
    ("k10temp", "Tctl"),  # AMD
    ("cpu_thermal", None),  # Raspberry Pi, Allwinner, Amlogic, RK3399 and other ARM boards
    ("soc_thermal", None),  # RK3588 under Rockchip's kernel: the sensor of the whole chip
    ("package_thermal", None),  # RK3588 under the mainline kernel: the same sensor
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
    for name, label in CPU_SENSORS:
        readings = groups.get(name)
        if readings:
            return next((r for r in readings if r.label == label), readings[0]).current
    return next((readings[0].current for readings in groups.values()), None)
