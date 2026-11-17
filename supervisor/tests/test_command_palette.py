"""How the command palette of the web UI narrows its choices (ADR-0388), run with node.

webui/src/lib/components/palette/match.ts ranks a choice by its names in every language and the
other words it is known by: a name that starts with the query first, then a name whose words
start with the query's words, then a name that holds the query, then the keywords. The palette
itself, its keyboard and its roles are the E2E spec's: webui/tests/command-palette.spec.ts.
Without node, or without webui/node_modules (make setup), the tests skip.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEBUI = Path(__file__).resolve().parents[2] / "webui"
MATCH = WEBUI / "src" / "lib" / "components" / "palette" / "match.ts"

# Loads match.ts with TypeScript's transpiler and prints what each call returns.
RUNNER = r"""
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const [, , packageJson, matchTs, callsJson] = process.argv;
const ts = createRequire(packageJson)('typescript');
const { outputText } = ts.transpileModule(readFileSync(matchTs, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
});
const match = await import('data:text/javascript;base64,' + Buffer.from(outputText).toString('base64'));
const results = JSON.parse(callsJson).map(([name, items, query]) =>
  name === 'narrow' ? match.narrow(items, query).map((item) => item.names[0]) : match[name](items, query));
process.stdout.write(JSON.stringify(results));
"""


def choice(*names: str, keywords: tuple[str, ...] = ()) -> dict:
    return {"names": list(names), "keywords": list(keywords)}


# Choices as the palette has them: the English name first, then the Russian one.
CHOICES = [
    choice("Dashboard", "Обзор"),
    choice("Storage", "Хранилище", keywords=("pools disks volumes", "пулы диски тома")),
    choice("App Store", "Каталог приложений"),
    choice("Audit Log", "Журнал аудита"),
    choice("System Logs", "Системные журналы"),
    choice("Disk Health", "Состояние дисков", keywords=("SMART disks", "SMART диски")),
    choice("Backup & Restore", "Резервные копии"),
    choice("Account", "Учётная запись", keywords=("password two-factor 2FA", "пароль двухфакторная 2FA")),
    choice("Reboot the host…", "Перезагрузить хост…", keywords=("restart", "перезапуск")),
    choice("Logout", "Выйти"),
]


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed (Node.js 22, webui/package.json)")
    if not (WEBUI / "node_modules" / "typescript" / "package.json").exists():
        pytest.skip("webui/node_modules has no typescript: make setup installs it")
    script = tmp_path_factory.mktemp("palette") / "match.mjs"
    script.write_text(RUNNER, encoding="utf-8")

    def call(*calls: list) -> list:
        result = subprocess.run(
            [node, str(script), str(WEBUI / "package.json"), str(MATCH), json.dumps(list(calls))],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    return call


def names(run, query: str) -> list[str]:
    return run(["narrow", CHOICES, query])[0]


class TestNarrow:
    def test_an_empty_query_keeps_every_choice_in_its_order(self, run):
        everything = [item["names"][0] for item in CHOICES]

        assert names(run, "") == everything
        assert names(run, "   ") == everything

    def test_a_prefix_of_a_name_comes_before_a_prefix_of_its_words_and_a_part_of_a_word(self, run):
        # Logout starts with "log", Audit Log and System Logs have a word that does, and the
        # list's order decides between those two.
        assert names(run, "log") == ["Logout", "Audit Log", "System Logs"]
        assert names(run, "stor") == ["Storage", "App Store", "Backup & Restore"]

    def test_a_part_of_a_word_is_found(self, run):
        assert names(run, "hboa") == ["Dashboard"]
        assert names(run, "estor") == ["Backup & Restore"]

    def test_every_word_of_the_query_must_be_found_in_any_order(self, run):
        assert names(run, "logs sys") == ["System Logs"]
        assert names(run, "sys audit") == []

    def test_the_name_in_either_language_is_found(self, run):
        assert names(run, "хранил") == ["Storage"]
        assert names(run, "журнал") == ["Audit Log", "System Logs"]
        assert names(run, "storage") == ["Storage"]

    def test_case_does_not_count_in_either_alphabet(self, run):
        assert names(run, "STORAGE") == ["Storage"]
        assert names(run, "ХРАНИЛИЩЕ") == ["Storage"]
        assert names(run, "  aUdIt   LOG ") == ["Audit Log"]

    def test_yo_and_ye_are_one_letter(self, run):
        assert names(run, "учетная") == ["Account"]
        assert names(run, "учётная") == ["Account"]
        assert run(["rank", choice("Учетная запись"), "учётн"]) == [0]

    def test_the_keywords_are_found_after_every_name(self, run):
        assert names(run, "smart") == ["Disk Health"]
        assert names(run, "restart") == ["Reboot the host…"]
        assert names(run, "пароль") == ["Account"]
        # "disks" is a keyword of Storage and of Disk Health, whose name starts with "disk": that
        # name comes first.
        assert names(run, "disk") == ["Disk Health", "Storage"]

    def test_nothing_is_found_for_words_that_no_choice_has(self, run):
        assert names(run, "zzzz") == []
        assert names(run, "log zzzz") == []

    def test_the_ranks(self, run):
        storage = CHOICES[1]
        assert run(
            ["rank", storage, "sto"],
            ["rank", choice("Backup & Restore"), "rest"],
            ["rank", choice("Backup & Restore"), "kup"],
            ["rank", storage, "vol"],
            ["rank", storage, "olum"],
            ["rank", storage, "nothing"],
            ["rank", choice("Backup & Restore"), "&"],
        ) == [0, 1, 2, 3, 4, None, 2]

    def test_fold(self, run):
        assert run(["fold", "  Учётная   ЗАПИСЬ ", None]) == ["учетная запись"]
