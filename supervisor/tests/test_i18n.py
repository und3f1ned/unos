"""The web UI in English and Russian (ADR-0273), run with node.

* webui/scripts/i18n-check.mjs, which make frontend runs: every text of a .svelte file comes from
  the catalogs, except in the files of webui/scripts/i18n-untranslated.txt; both halves of every
  catalog have the same keys, plural forms and placeholders; every key of a $t call exists. Each
  test builds a small web UI in tmp_path and checks it, and the real one passes.
* webui/src/lib/i18n/translate.ts, the lookup behind $t: the text in the language, else in
  English, else the key; the plural form of the count; the placeholders.

Without node, or without webui/node_modules (make setup), the tests skip.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WEBUI = REPO / "webui"
CHECK = WEBUI / "scripts" / "i18n-check.mjs"
TRANSLATE = WEBUI / "src" / "lib" / "i18n" / "translate.ts"


@pytest.fixture(scope="module")
def node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed (Node.js 22, webui/package.json)")
    for package in ("svelte", "typescript"):
        if not (WEBUI / "node_modules" / package / "package.json").exists():
            pytest.skip(f"webui/node_modules has no {package}: make setup installs it")
    return node


def run_check(node: str, webui: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [node, str(CHECK), *([str(webui)] if webui else [])], capture_output=True, text=True, timeout=120
    )


DEMO_CATALOG = """\
import type { Translation } from '../translate';

export const en = {
	'demo.title': 'Shares',
	'demo.files': { one: '{n} file', other: '{n} files' },
	'demo.greeting': 'Hello, {name}'
};

export const ru: Translation<typeof en> = {
	'demo.title': 'Общие папки',
	'demo.files': { one: '{n} файл', few: '{n} файла', many: '{n} файлов' },
	'demo.greeting': 'Здравствуйте, {name}'
};
"""

# Everything here comes from the catalog, or holds no letters, or is code.
TRANSLATED = """\
<script lang="ts">
	import { t } from '$lib/i18n';
	export let count: number = 2;
	export let busy = false;
	export let level = 'write';
	let error = '';
	// Classes by variant: an error or a success here names a variant, not a text.
	const classes = { error: 'text-red-400 bg-red-500/20', success: 'text-green-400 font-medium' };
	const ZONE_HINT = 'task-time-zone-hint';
	console.error('Logged for developers only');
	$: label = busy ? $t('demo.title') : $t('demo.greeting', { name: 'Ann' });
	function fail() {
		error = $t('demo.title');
	}
</script>

<!-- A comment in English. -->
<h1 class="text-lg font-bold" title={$t('demo.title')}>{$t('demo.title')}</h1>
<p>{$t('demo.files', { n: count })} · 42% {count}</p>
<p class={classes.error}>{level === 'write' ? $t('demo.title') : label}</p>
<input aria-describedby={ZONE_HINT} placeholder="000000" aria-label={$t('demo.title')} />
<Input label={$t('demo.title')} variant="secondary" on:click={() => fail()} />
<a href="/storage">&rarr;</a>

<style>
	p { color: red; }
</style>
"""

PENDING = "<p>Not translated yet</p>\n"


def make_webui(root: Path, *, files: dict[str, str] | None = None, catalog: str = DEMO_CATALOG,
               listed: tuple[str, ...] = ("src/routes/pending.svelte",)) -> Path:
    """A web UI with the catalog demo.ts, the translated done.svelte and the listed pending.svelte."""
    webui = root / "webui"
    sources = {"src/routes/done.svelte": TRANSLATED, "src/routes/pending.svelte": PENDING, **(files or {})}
    for name, text in {**sources, "src/lib/i18n/catalog/demo.ts": catalog}.items():
        (webui / name).parent.mkdir(parents=True, exist_ok=True)
        (webui / name).write_text(text, encoding="utf-8")
    (webui / "scripts").mkdir(exist_ok=True)
    (webui / "scripts" / "i18n-untranslated.txt").write_text(
        "# Not translated yet.\n\n" + "".join(f"{name}\n" for name in listed), encoding="utf-8"
    )
    return webui


class TestTheWebUI:
    def test_the_web_ui_passes(self, node):
        result = run_check(node)

        assert result.returncode == 0, result.stderr
        assert "keys in en and ru" in result.stdout

    def test_make_frontend_runs_the_check(self):
        package = json.loads((WEBUI / "package.json").read_text(encoding="utf-8"))
        makefile = (REPO / "Makefile").read_text(encoding="utf-8")

        assert package["scripts"]["i18n"] == "node scripts/i18n-check.mjs"
        assert "frontend-check frontend-i18n frontend-build" in makefile
        assert "frontend-i18n:\n\tcd webui && $(NPM_BIN) run i18n\n" in makefile

    def test_a_small_web_ui_passes(self, node, tmp_path):
        result = run_check(node, make_webui(tmp_path))

        assert result.returncode == 0, result.stderr
        assert "3 keys in en and ru; 2 .svelte files, 1 of them not translated yet" in result.stdout


# Markup and scripts with a text that does not come from the catalog, and the text reported.
UNTRANSLATED = [
    ("<p>Plain English</p>", "Plain English"),
    ('<input placeholder="Search files" />', "Search files"),
    ("<button title=\"Delete\">{$t('demo.title')}</button>", "Delete"),
    ('<img alt="Logo" src="/logo.png" />', "Logo"),
    ('<nav aria-label="Main menu"></nav>', "Main menu"),
    ('<Input label="Name" />', "Name"),
    ('<Modal title="Delete the share" />', "Delete the share"),
    ('<EmptyState description="Nothing here" />', "Nothing here"),
    ('<Picker reduceOnlyHint="Only lower" />', "Only lower"),
    ("<Input ariaLabel={'Filter'} />", "Filter"),
    ("<p title=\"{n} items\">{$t('demo.title')}</p>", "items"),
    ("<p>{busy ? 'Saving...' : $t('demo.title')}</p>", "Saving..."),
    ("<p>{port || 'Any'}</p>", "Any"),
    ("<p>{`${n} files`}</p>", "files"),
    ("<p>{$t('demo.title') + ' more'}</p>", "more"),
    ("<p>{('none' || x).toUpperCase()}</p>", "none"),
    ('<input type="submit" value="Save" />', "Save"),
    ("<option value=\"tcp\">TCP</option>", "TCP"),
    ("<script lang=\"ts\">\n\tlet error = '';\n\terror = 'Passwords do not match';\n</script>", "Passwords do not match"),
    ("<script lang=\"ts\">\n\tconst items = [{ href: '/', label: 'Dashboard' }];\n</script>", "Dashboard"),
    ("<script lang=\"ts\">\n\tconst REFUSED_TITLE = 'Nothing was made';\n</script>", "Nothing was made"),
    ("<script lang=\"ts\">\n\texport let title: string = 'No data';\n</script>", "No data"),
    ("<script lang=\"ts\">\n\t$: leftText = `${n} codes left`;\n</script>", "codes left"),
    ("<script lang=\"ts\">\n\tlet notice = '';\n\tnotice = ok ? $t('demo.title') : 'Failed';\n</script>", "Failed"),
    ("<script lang=\"ts\">\n\tif (!confirm('Delete this rule?')) {}\n</script>", "Delete this rule?"),
]


class TestTexts:
    @pytest.mark.parametrize(("markup", "text"), UNTRANSLATED)
    def test_a_text_outside_the_catalog_is_reported(self, node, tmp_path, markup, text):
        webui = make_webui(tmp_path, files={"src/routes/new.svelte": markup + "\n"})

        result = run_check(node, webui)

        assert result.returncode == 1
        reported = [line for line in result.stderr.splitlines() if line.startswith("src/routes/new.svelte:")]
        assert len(reported) == 1, result.stderr
        assert "untranslated text" in reported[0] and json.dumps(text) in reported[0], reported

    def test_a_report_names_the_line_and_the_column_of_the_text(self, node, tmp_path):
        webui = make_webui(tmp_path, files={"src/routes/new.svelte": "<div>\n\t<p>\n\t\tPlain English\n\t</p>\n</div>\n"})

        result = run_check(node, webui)

        assert 'src/routes/new.svelte:3:3: untranslated text "Plain English"' in result.stderr.splitlines()

    def test_the_texts_of_a_listed_file_are_not_reported(self, node, tmp_path):
        # A component has one instance script: the markup of every case, and the script of one.
        markup = "".join(m + "\n" for m, _ in UNTRANSLATED if not m.startswith("<script"))
        script = next(m for m, _ in UNTRANSLATED if m.startswith("<script"))
        webui = make_webui(tmp_path, files={"src/routes/pending.svelte": script + "\n" + markup})

        result = run_check(node, webui)

        assert result.returncode == 0, result.stderr

    def test_a_listed_file_without_untranslated_texts_must_leave_the_list(self, node, tmp_path):
        webui = make_webui(tmp_path, listed=("src/routes/pending.svelte", "src/routes/done.svelte"))

        result = run_check(node, webui)

        assert result.returncode == 1
        assert ("scripts/i18n-untranslated.txt:4: src/routes/done.svelte has no untranslated text: "
                "take it off the list") in result.stderr.splitlines()

    @pytest.mark.parametrize("entry", ["src/routes/gone.svelte", "src/lib/i18n/catalog/demo.ts"])
    def test_a_listed_entry_must_be_a_svelte_file(self, node, tmp_path, entry):
        webui = make_webui(tmp_path, listed=("src/routes/pending.svelte", entry))

        result = run_check(node, webui)

        assert result.returncode == 1
        assert f"scripts/i18n-untranslated.txt:4: {entry} is not a .svelte file under src/" in result.stderr


class TestKeys:
    @pytest.mark.parametrize("markup", [
        "<p>{$t('demo.missing')}</p>",
        "<script lang=\"ts\">\n\tconst x: string = $t('demo.missing');\n</script>",
        "<Input label={$t('demo.missing')} />",
    ])
    def test_a_key_that_no_catalog_has_is_reported(self, node, tmp_path, markup):
        webui = make_webui(tmp_path, files={"src/routes/new.svelte": markup + "\n"})

        result = run_check(node, webui)

        assert result.returncode == 1
        assert "$t('demo.missing'): no such key in the catalogs" in result.stderr

    @pytest.mark.parametrize(("markup", "reported"), [
        ("<p>{$t('demo.files')}</p>", True),
        ("<p>{$t('demo.files', { name: 'x' })}</p>", True),
        ("<p>{$t('demo.files', { n: 2 })}</p>", False),
        ("<p>{$t('demo.files', { n })}</p>", False),
        ("<p>{$t('demo.files', params)}</p>", False),
        ("<script lang=\"ts\">\n\tconst x = $t('demo.files');\n</script>", True),
        ("<script lang=\"ts\">\n\tconst x = $t('demo.files', { n: 1 });\n</script>", False),
    ])
    def test_a_text_with_plural_forms_needs_its_count(self, node, tmp_path, markup, reported):
        webui = make_webui(tmp_path, files={"src/routes/new.svelte": markup + "\n"})

        result = run_check(node, webui)

        assert (result.returncode == 1) is reported, result.stderr
        assert ("a text with plural forms needs its count" in result.stderr) is reported


def replace(catalog: str, old: str, new: str) -> str:
    assert old in catalog
    return catalog.replace(old, new)


# Catalogs with a mistake, and what the check says about it.
BROKEN_CATALOGS = [
    (replace(DEMO_CATALOG, "\t'demo.greeting': 'Здравствуйте, {name}'\n", ""), "demo.greeting: missing in ru"),
    (replace(DEMO_CATALOG, "'Здравствуйте, {name}'", "'Здравствуйте, {name}',\n\t'demo.extra': 'Лишний'"),
     "demo.extra: in ru but not in en"),
    (replace(DEMO_CATALOG, "'demo.title': 'Shares'", "'shares.title': 'Shares'"),
     'shares.title: a key of this catalog starts with "demo."'),
    (replace(DEMO_CATALOG, "'Здравствуйте, {name}'", "'Здравствуйте'"),
     "demo.greeting: placeholders {name} in en, {} in ru"),
    (replace(DEMO_CATALOG, "few: '{n} файла', ", ""), "demo.files (ru): plural forms many, one; ru has few, many, one"),
    (replace(DEMO_CATALOG, "{ one: '{n} file', other: '{n} files' }", "{ one: '{n} file', few: '{n} files', other: '{n} files' }"),
     "demo.files (en): plural forms few, one, other; en has one, other"),
    (replace(DEMO_CATALOG, "'demo.title': 'Общие папки'", "'demo.title': ' '"), "demo.title (ru): an empty text"),
    (replace(DEMO_CATALOG, "'demo.title': 'Общие папки'", "'demo.title': { one: 'Папка', few: 'Папки', many: 'Папок' }"),
     "demo.title: a text in one language and plural forms in the other"),
    (replace(DEMO_CATALOG, "export const ru", "export const de"), "exports no object ru"),
    ("import { en as other } from './other';\nexport const en = other;\nexport const ru = other;\n", "cannot be loaded"),
]


class TestCatalogs:
    @pytest.mark.parametrize(("catalog", "message"), BROKEN_CATALOGS)
    def test_a_broken_catalog_is_reported(self, node, tmp_path, catalog, message):
        result = run_check(node, make_webui(tmp_path, catalog=catalog))

        assert result.returncode == 1
        assert any(line.startswith("src/lib/i18n/catalog/demo.ts: ") and message in line
                   for line in result.stderr.splitlines()), result.stderr

    def test_the_first_areas_have_catalogs(self):
        # The areas the first part of the feature translated (ADR-0273); others join them.
        catalogs = sorted(path.stem for path in (WEBUI / "src" / "lib" / "i18n" / "catalog").glob("*.ts"))

        assert {"account", "common", "layout", "login", "setup", "system", "ui"} <= set(catalogs)


# Runs the lookup of translate.ts over a few catalogs and prints what each call returns.
LOOKUP = r"""
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const [, , packageJson, translateTs, callsJson] = process.argv;
const ts = createRequire(packageJson)('typescript');
const { outputText } = ts.transpileModule(readFileSync(translateTs, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
});
const i18n = await import('data:text/javascript;base64,' + Buffer.from(outputText).toString('base64'));
const catalogs = {
  en: {
    'demo.title': 'Shares',
    'demo.onlyEnglish': 'Only in English',
    'demo.greeting': 'Hello, {name}',
    'demo.files': { one: '{n} file', other: '{n} files' },
    'demo.inherited': 'a {toString} b',
  },
  ru: {
    'demo.title': 'Общие папки',
    'demo.greeting': 'Здравствуйте, {name}',
    'demo.files': { one: '{n} файл', few: '{n} файла', many: '{n} файлов' },
    'demo.inherited': 'а {toString} б',
  },
};
const results = JSON.parse(callsJson).map(([name, ...args]) =>
  name === 'translate' ? i18n.translate(catalogs, ...args) : i18n[name](...args));
process.stdout.write(JSON.stringify(results));
"""


@pytest.fixture(scope="module")
def lookup(node, tmp_path_factory):
    script = tmp_path_factory.mktemp("lookup") / "lookup.mjs"
    script.write_text(LOOKUP, encoding="utf-8")

    def call(*calls: list) -> list:
        result = subprocess.run(
            [node, str(script), str(WEBUI / "package.json"), str(TRANSLATE), json.dumps(list(calls))],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    return call


class TestLookup:
    def test_a_text_comes_in_the_language_else_in_english_else_as_its_key(self, lookup):
        assert lookup(
            ["translate", "ru", "demo.title"],
            ["translate", "en", "demo.title"],
            ["translate", "ru", "demo.onlyEnglish"],
            ["translate", "ru", "demo.missing"],
            ["translate", "en", "demo.missing"],
            ["translate", "ru", "constructor"],
        ) == ["Общие папки", "Shares", "Only in English", "demo.missing", "demo.missing", "constructor"]

    def test_placeholders_take_the_values_given(self, lookup):
        assert lookup(
            ["translate", "ru", "demo.greeting", {"name": "Анна"}],
            ["translate", "en", "demo.greeting", {"name": "Ann", "other": 1}],
            ["translate", "en", "demo.greeting"],
            ["translate", "ru", "demo.inherited", {}],
        ) == ["Здравствуйте, Анна", "Hello, Ann", "Hello, {name}", "а {toString} б"]

    def test_a_count_takes_its_plural_form(self, lookup):
        counts = [0, 1, 2, 4, 5, 11, 12, 21, 22, 25, 101, 111]

        russian = lookup(*[["translate", "ru", "demo.files", {"n": n}] for n in counts])
        english = lookup(*[["translate", "en", "demo.files", {"n": n}] for n in (0, 1, 2, 21)])

        assert russian == ["0 файлов", "1 файл", "2 файла", "4 файла", "5 файлов", "11 файлов", "12 файлов",
                           "21 файл", "22 файла", "25 файлов", "101 файл", "111 файлов"]
        assert english == ["0 files", "1 file", "2 files", "21 files"]

    def test_the_languages(self, lookup):
        assert lookup(["isLocale", "ru"], ["isLocale", "en"], ["isLocale", "de"], ["isLocale", None]) == [
            True, True, False, False]


def test_the_check_reads_scripts_with_typescript(node, tmp_path):
    # A <script lang="ts"> with types, generics and a module script parses: Svelte's parser
    # reads only the markup.
    script = textwrap.dedent("""\
        <script context="module" lang="ts">
        	export type Row = { id: number };
        </script>
        <script lang="ts">
        	import type { Writable } from 'svelte/store';
        	export let rows: Array<Row> = [];
        	let store: Writable<number> | null = null;
        	const pick = <T,>(items: T[]): T | undefined => items[0];
        </script>
        <p>{rows.length}</p>
        """)
    webui = make_webui(tmp_path, files={"src/routes/typed.svelte": script})

    result = run_check(node, webui)

    assert result.returncode == 0, result.stderr
