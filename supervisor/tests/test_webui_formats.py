"""The one home of the web UI's numbers and dates (ADR-0459), run with node.

webui/src/lib/format.ts writes numbers, sizes, rates, percentages, temperatures, durations and ages,
and webui/src/lib/time.ts dates and times, in the format of the page's language: a decimal point and
"90.3%" in English, a decimal comma and "90,3 %" in Russian, the units of the catalog `format`, one
date and time to the minute, and a time of the NAS's clock in its zone (ADR-0432). The design check
fails for a format made by hand elsewhere (test_webui_design_system.py).

Without node, or without webui/node_modules (make setup), the tests skip.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WEBUI = REPO / "webui"
SRC = WEBUI / "src" / "lib"
NBSP = " "

# Transpiles time.ts, format.ts and the catalog `format`, links format.ts's import of $lib/time to
# time.ts, and prints what each call returns. A call's `t` looks the key up in the catalog's half of
# its locale and fills its placeholders.
RUN = r"""
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const [, , packageJson, timeTs, formatTs, catalogTs, callsJson] = process.argv;
const ts = createRequire(packageJson)('typescript');
const load = async (file, replace = (text) => text) => {
  const { outputText } = ts.transpileModule(readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  });
  return [replace(outputText), await import('data:text/javascript;base64,' + Buffer.from(replace(outputText)).toString('base64'))];
};
const [timeJs, time] = await load(timeTs);
const timeUrl = 'data:text/javascript;base64,' + Buffer.from(timeJs).toString('base64');
const [, format] = await load(formatTs, (text) => text.replace("'$lib/time'", JSON.stringify(timeUrl)));
const [, catalog] = await load(catalogTs, (text) => text.replace(/^import .*$/m, ''));
const t = (half) => (key, params = {}) =>
  (catalog[half][key] ?? key).replace(/\{(\w+)\}/g, (_, name) => String(params[name]));
const results = JSON.parse(callsJson).map(([name, ...args]) => {
  const fn = format[name] ?? time[name];
  const filled = args.map((arg) => (arg && typeof arg === 'object' && arg.t ? t(arg.t) : arg));
  return fn(...filled);
});
process.stdout.write(JSON.stringify(results));
"""


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed (Node.js 22, webui/package.json)")
    if not (WEBUI / "node_modules" / "typescript" / "package.json").exists():
        pytest.skip("webui/node_modules has no typescript: make setup installs it")
    script = tmp_path_factory.mktemp("formats") / "run.mjs"
    script.write_text(RUN, encoding="utf-8")

    def call(*calls: list) -> list:
        result = subprocess.run(
            [node, str(script), str(WEBUI / "package.json"), str(SRC / "time.ts"), str(SRC / "format.ts"),
             str(SRC / "i18n" / "catalog" / "format.ts"), json.dumps(list(calls))],
            capture_output=True, text=True, timeout=60, env={"TZ": "Asia/Kathmandu", "PATH": ""},
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    return call


def spaced(texts: list[str]) -> list[str]:
    """The texts with each kind of space as a plain one, where ICU's versions differ (the narrow
    no-break space before AM of newer ones)."""
    return [text.replace(NBSP, " ").replace("\u202f", " ") for text in texts]


EN = {"t": "en"}
RU = {"t": "ru"}


class TestNumbers:
    def test_a_number_takes_the_decimal_separator_of_the_language_without_grouping(self, run):
        assert run(
            ["formatNumber", 1.25, "en-US", {"digits": 1}],
            ["formatNumber", 1.25, "ru", {"digits": 1}],
            ["formatNumber", 2, "ru", {"digits": 1, "fixed": True}],
            ["formatNumber", 12345.5, "en-US", {"digits": 1}],
            ["formatNumber", 12345.5, "ru", {"digits": 1}],
            ["formatNumber", 8192, "en-US", {"grouping": True}],
            ["formatNumber", -0.04, "en-US", {"digits": 1}],
        ) == ["1.3", "1,3", "2,0", "12345.5", "12345,5", "8,192", "0"]

    def test_a_percentage_is_written_as_the_language_writes_it(self, run):
        assert run(
            ["formatPercent", 90.34, "en-US", {"digits": 1}],
            ["formatPercent", 90.34, "ru", {"digits": 1}],
            ["formatPercent", 25, "en-GB", {"digits": 1, "fixed": True}],
            ["formatPercent", 25, "ru", {"digits": 1, "fixed": True}],
            ["formatPercent", 12.6, "ru"],
        ) == ["90.3%", f"90,3{NBSP}%", "25.0%", f"25,0{NBSP}%", f"13{NBSP}%"]

    def test_a_temperature_is_written_as_the_language_writes_it(self, run):
        # ICU's data puts a space or a no-break space in Russian: the space is the language's.
        assert spaced(run(["formatTemperature", 45.4, "en-US"], ["formatTemperature", 45.4, "ru"])) == ["45°C", "45 °C"]


class TestSizes:
    def test_a_size_takes_the_largest_unit_and_the_units_of_the_catalog(self, run):
        assert run(
            ["formatSize", 1536, "en-US", EN],
            ["formatSize", 1536, "ru", RU],
            ["formatSize", 2.5 * 1024**3, "ru", RU, {"digits": 2}],
            ["formatSize", 1024**3, "en-US", EN, {"digits": 1, "fixed": True}],
            ["formatSize", 0, "ru", RU],
            ["formatSize", 3 * 1024**5, "en-US", EN],
        ) == ["1.5 KB", "1,5 КБ", "2,5 ГБ", "1.0 GB", "0 Б", "3072 TB"]

    def test_a_rate_is_a_size_per_second(self, run):
        assert run(["formatRate", 1.5 * 1024**2, "en-US", EN], ["formatRate", 1.5 * 1024**2, "ru", RU]) == [
            "1.5 MB/s",
            "1,5 МБ/с",
        ]

    def test_a_unit_that_the_server_names_has_its_key(self, run):
        assert run(["sizeUnitKey", "TB"], ["sizeUnitKey", "B"], ["sizeUnitKey", "TiB"]) == [
            "format.size.tb",
            "format.size.b",
            None,
        ]


class TestDurations:
    def test_a_duration_starts_at_its_largest_unit(self, run):
        day = 86400
        assert run(
            ["formatDuration", 3 * day + 4 * 3600 + 5 * 60, "en-US", EN],
            ["formatDuration", 3 * day + 4 * 3600 + 5 * 60, "en-US", EN, {"parts": 3}],
            ["formatDuration", 3 * day + 4 * 3600 + 5 * 60, "ru", RU],
            ["formatDuration", 27 * 3600 + 27 * 60, "ru", RU],
            ["formatDuration", 4 * 3600 + 5 * 60 + 7, "en-US", EN, {"parts": 3}],
            ["formatDuration", 59, "en-US", EN],
        ) == ["3d 4h", "3d 4h 5m", f"3{NBSP}дн. 4{NBSP}ч", f"1{NBSP}дн. 3{NBSP}ч", "4h 5m", "0m"]

    def test_a_short_duration_in_seconds_keeps_its_decimals_and_one_below_a_second_is_in_milliseconds(self, run):
        options = {"smallest": "s", "digits": 1}
        assert run(
            ["formatDuration", 64, "en-US", EN, {"smallest": "s"}],
            ["formatDuration", 1.54, "en-US", EN, options],
            ["formatDuration", 1.54, "ru", RU, options],
            ["formatDuration", 0.011, "en-US", EN, options],
            ["formatDuration", 0.011, "ru", RU, options],
            ["formatDuration", 59.96, "en-US", EN, options],
            ["formatDuration", 0.9996, "en-US", EN, options],
        ) == ["1m 4s", "1.5s", f"1,5{NBSP}с", "11ms", f"11{NBSP}мс", "1m 0s", "1s"]


class TestDates:
    # 03:04:05 UTC on 7 November 2026 is 08:49:05 in Kathmandu (UTC+05:45), the zone of the runs.
    MOMENT = "2026-11-07T03:04:05+00:00"

    def test_a_date_and_time_has_one_format_per_language_to_the_minute(self, run):
        assert spaced(run(
            ["formatDateTime", self.MOMENT, "en-US"],
            ["formatDateTime", self.MOMENT, "en-GB"],
            ["formatDateTime", self.MOMENT, "ru"],
            ["formatDateTime", "2026-11-07T23:04:05Z", "ru"],
        )) == ["11/7/2026, 8:49 AM", "07/11/2026, 08:49", "07.11.2026, 08:49", "08.11.2026, 04:49"]

    def test_the_lines_of_a_log_show_the_seconds(self, run):
        assert spaced(run(
            ["formatDateTime", self.MOMENT, "en-US", {"seconds": True}],
            ["formatDateTime", self.MOMENT, "ru", {"seconds": True}],
            ["formatTimeOfDay", self.MOMENT, "ru", {"seconds": True}],
        )) == ["11/7/2026, 8:49:05 AM", "07.11.2026, 08:49:05", "08:49:05"]

    def test_a_time_of_the_nas_clock_is_shown_in_its_zone(self, run):
        assert spaced(run(
            ["formatDateTime", self.MOMENT, "ru", {"zone": "Europe/Berlin"}],
            ["formatTimeOfDay", self.MOMENT, "en-US", {"zone": "Europe/Berlin"}],
            ["formatDateTime", self.MOMENT, "ru", {"zone": "Unknown"}],
        )) == ["07.11.2026, 04:04", "4:04 AM", "07.11.2026, 08:49"]

    def test_a_date_a_time_of_day_and_a_clock_time_are_its_parts(self, run):
        assert spaced(run(
            ["formatDate", self.MOMENT, "en-US"],
            ["formatDate", self.MOMENT, "ru"],
            ["formatTimeOfDay", self.MOMENT, "en-US"],
            ["formatTimeOfDay", self.MOMENT, "ru"],
            ["formatClock", 2, 0, "en-US"],
            ["formatClock", 2, 0, "ru"],
            ["formatClock", 23, 5, "en-GB"],
        )) == ["11/7/2026", "07.11.2026", "8:49 AM", "08:49", "2:00 AM", "02:00", "23:05"]

    def test_a_value_without_an_offset_is_shown_as_it_comes(self, run):
        assert spaced(run(
            ["formatDateTime", "2026-11-07 03:04:05", "ru"],
            ["formatDate", "soon", "en-US"],
            ["formatTimeOfDay", None, "ru"],
            ["formatAgo", "yesterday", "en-US", EN],
        )) == ["2026-11-07 03:04:05", "soon", "", "yesterday"]

    def test_an_age_within_a_day_is_how_long_ago_and_an_older_one_its_date(self, run):
        now = datetime.now(UTC)
        ago = [(now - delta).isoformat() for delta in (timedelta(seconds=20), timedelta(minutes=5), timedelta(hours=3))]
        old = "2026-01-02T03:04:05+00:00"
        assert spaced(run(
            *[["formatAgo", value, "en-US", EN] for value in ago],
            *[["formatAgo", value, "ru", RU] for value in ago],
            ["formatAgo", old, "ru", RU],
        )) == ["Just now", "5m ago", "3h ago", "Только что", "5 мин назад", "3 ч назад", "02.01.2026"]

    def test_a_day_heading_is_written_in_full(self, run):
        assert spaced(run(["formatDayHeading", "2026-11-07", "en-US"], ["formatDayHeading", "2026-11-07", "ru"])) == [
            "Saturday, November 7, 2026",
            "суббота, 7 ноября 2026 г.",
        ]
