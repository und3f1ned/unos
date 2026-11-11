---
status: accepted
date: 2026-11-11
area: system
decided-by: agent
---

# ADR-0346: The dashboard shows the CPU's or SoC's temperature, chosen by the sensor's name in one place on the server

## Context

`GET /api/v1/system/stats` and the stats message of `/ws/v1/events` each sent the first reading of every sensor group that `psutil.sensors_temperatures()` lists, and the dashboard's Temperature card showed the first of them. psutil lists the groups in the order of their hwmon paths, so on many hosts the card showed an ACPI zone (`acpitz`), an NVMe disk (`nvme`) or a SATA disk (`drivetemp`) instead of the CPU (`system.temperatures`).

Step 1 of that entry said "The dashboard's CPU card shows the temperature of the host's CPU", but the temperature is on the dashboard's Temperature card, and the CPU card shows the CPU's usage. Working on through the features under the owner's delegation ("work on through the features, ask no questions; I allow everything", in translation), the agent read the step as the Temperature card, reworded it to "The dashboard's Temperature card shows the temperature of the host's CPU", and made the choices below.

## Decision

- `cpu_temperature()` of `supervisor/src/system/sensors.py` alone chooses the temperature: the reading of the first group of `CPU_SENSORS` that psutil lists, in this order: `coretemp` (Intel, the reading labelled `Package id 0`), `k10temp` (AMD, `Tdie`, else `Tctl`), `cpu_thermal` (Raspberry Pi and other ARM boards), `soc_thermal` (the RK3588 under Rockchip's kernel, the sensor of the whole chip) and `package_thermal` (the same sensor under the mainline kernel); in a group without one of these labels, its first reading. `CPU_SENSORS` gives each group its labels in order of preference. On the Ryzen X CPUs of the first two generations and the Threadripper 1000 and 2000, `Tctl` is the die's temperature plus an offset of 10 to 27 degrees, and k10temp lists `Tdie`, the die's own, beside it; on the other AMD CPUs it lists `Tctl` alone, which is the die's temperature. A group's name is compared with "-" read as "_": the kernel names a thermal zone's hwmon device after the zone with "_" for "-", and on a host without hwmon psutil names the groups after the zones themselves (`soc-thermal`).
- On a host without a known group, the first reading of the first group, as before; without sensors, or when psutil cannot read them (macOS has no `sensors_temperatures`), none.
- `GET /api/v1/system/stats` answers it as `cpu_temperature`, and the stats message as `cpu.temperature` (a number in degrees Celsius or null). Their `temperatures` objects are gone (ADR-0207): the dashboard read only the first value, and `GET /api/v1/system/temperatures` lists every sensor with its readings.
- The dashboard (`webui/src/routes/+page.svelte`) shows `cpu.temperature` rounded to a whole degree, and N/A when it is null.
- Tests: `TestDashboardTemperature` of `supervisor/tests/test_system.py` (the groups of an Intel host, of AMD hosts with `Tctl` alone and with `Tdie` beside it, of a Raspberry Pi and of an RK3588 host, both spellings of the zones, the fallback and no sensors, through both `/system/stats` and the stats message), `supervisor/tests/test_ws_stats.py` (the message's shape), `webui/tests/dashboard.spec.ts` (the card with a temperature and with none).

## Consequences

- A CPU or SoC whose sensor group has another name shows the first group's reading, which may be another chip's or a disk's, until its name joins `CPU_SENSORS`.
- The card does not say which sensor it shows; System has no page that lists the sensors yet.
