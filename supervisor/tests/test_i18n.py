"""The web UI in English and Russian (ADR-0287), run with node.

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

# Everything here comes from the catalog, or holds no letters, or is code: a module, a type, a
# compared or looked-up value, a key, a name, an id, a class, a path, a MIME type, a locale, a
# key of the catalogs, a developer's message, or a literal exempted with its reason.
TRANSLATED = """\
<script lang="ts">
	import { t } from '$lib/i18n';
	import Widget from './Widget.svelte';
	type Props = { count?: number; busy?: boolean; level?: string; mode?: 'primary' | 'Read only'; user?: string };
	let { count = 2, busy = false, level = 'write', mode = 'primary', user = '' }: Props = $props();
	let error = $state('');
	// Classes by variant: an error or a success here names a variant, not a text.
	const classes = { error: 'text-red-400 bg-red-500/20', success: 'text-green-400 font-medium' };
	const badgeClass = 'btn btn-primary';
	const ZONE_HINT = 'task-time-zone-hint';
	const KEYS = new Set(['ArrowDown', 'ArrowUp']);
	const LABELS: Record<string, string> = { allow: 'demo.title', deny: 'demo.greeting' };
	const headers = { 'Content-Type': 'application/json', Accept: 'text/plain' };
	console.error('Logged for developers only');
	const label = $derived(busy ? $t('demo.title') : $t('demo.greeting', { name: user }));
	const path = $derived(`/api/v1/shares/${count}#top`);
	const inputId = $derived.by(() => `select-${count}`);
	function fail() {
		error = $t('demo.title');
	}
	function onKey(e: KeyboardEvent, ua: string, row: Record<string, string>) {
		if (e.key === 'Escape' || ['Enter', 'Space'].includes(e.key) || ua.includes('Chrome')) fail();
		switch (row['Display Name']) {
			case 'Tab':
				break;
		}
		return new Intl.DateTimeFormat('en-GB', { month: 'long' }).format(new Date());
	}
	// i18n-ignore: a command, the same in every language
	const command = $derived(`sudo mount -t cifs //nas/${level}`);
</script>

<!-- A comment in English. -->
<h1 class="text-lg font-bold" title={$t('demo.title')}>{$t('demo.title')}</h1>
<p>{$t('demo.files', { n: count })} · 42% {count}</p>
<p class={classes.error}>{level === 'write' ? $t('demo.title') : label}</p>
<input aria-describedby={ZONE_HINT} placeholder="000000" aria-label={$t('demo.title')} />
<Input label={$t('demo.title')} variant="secondary" onclick={() => fail()} />
<a href="/storage">&rarr;</a>
{#if mode === 'Read only'}
	<p>{$t(LABELS[level])}</p>
{/if}
<Widget variant={busy ? 'success' : 'error'} size="sm" class="mt-3 flex" onclose={() => fail()} />
{#snippet row(id: string, variant = 'secondary')}
	<p id="row-{id}" class={variant}>{$t('demo.title')}</p>
{/snippet}
{@render row('demo-row')}
<Widget>
	{#snippet footer()}<span>{$t('demo.title')}</span>{/snippet}
</Widget>
<a href={`/shares/${count}`} class={busy ? 'nav-item nav-item-active' : 'nav-item'}>{$t('demo.title')}</a>
<input type={busy ? 'text' : 'password'} autocomplete="one-time-code" />

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
        assert "frontend-check frontend-runes frontend-i18n frontend-design frontend-build" in makefile
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
    ("<script lang=\"ts\">\n\tlet { title = 'No data' }: { title?: string } = $props();\n</script>", "No data"),
    ("<script lang=\"ts\">\n\tconst leftText = $derived(`${n} codes left`);\n</script>", "codes left"),
    ("<script lang=\"ts\">\n\tlet notice = '';\n\tnotice = ok ? $t('demo.title') : 'Failed';\n</script>", "Failed"),
    ("<script lang=\"ts\">\n\tif (!confirm('Delete this rule?')) {}\n</script>", "Delete this rule?"),
    # A literal of a script is a text wherever it goes, unless it is code (TRANSLATED).
    ("<script lang=\"ts\">\n\tconst LOG_STATE_TEXT = { connecting: 'Connecting to the log...' };\n</script>",
     "Connecting to the log..."),
    ("<script lang=\"ts\">\n\tconst ACTIONS = { reboot: { confirm: 'Reboot in 1 minute' } };\n</script>",
     "Reboot in 1 minute"),
    ("<script lang=\"ts\">\n\tfunction open() {\n\t\tshowError('Could not open the folder', e);\n\t}\n</script>",
     "Could not open the folder"),
    ("<script lang=\"ts\">\n\tconst blockers: string[] = [];\n\tblockers.push('forced read-only');\n</script>",
     "forced read-only"),
    ("<script lang=\"ts\">\n\tlet problem = '';\n\tproblem = 'The pool is degraded';\n</script>", "The pool is degraded"),
    ("<script lang=\"ts\">\n\tfunction browser() {\n\t\treturn 'Chrome';\n\t}\n</script>", "Chrome"),
    ("<script lang=\"ts\">\n\tthrow new Error('The share has no path');\n</script>", "The share has no path"),
    # A placeholder's value is shown: even a single lower-case word.
    ("<script lang=\"ts\">\n\tconst state = $t('demo.greeting', { name: 'guest' });\n</script>", "guest"),
    # An exemption needs its reason.
    ("<script lang=\"ts\">\n\t// i18n-ignore\n\tconst brand = 'Chrome Browser';\n</script>", "Chrome Browser"),
    # The same in the expressions of the markup: blocks, tags, directives and props.
    ("<p>{$t('demo.greeting', { name: 'Ann' })}</p>", "Ann"),
    ("<p>{describe(e, 'Installation failed')}</p>", "Installation failed"),
    ("<p>{{ allow: 'Allowed' }[action]}</p>", "Allowed"),
    ("{#if ok}\n\t{@const note = 'Free space'}\n\t<p>{note}</p>\n{/if}", "Free space"),
    ("{#each ['Disks'] as tab}\n\t<p>{tab}</p>\n{/each}", "Disks"),
    ("<button onclick={() => (notice = 'Saved')}>{$t('demo.title')}</button>", "Saved"),
    # Snippets: their markup, the defaults of their parameters and what {@render} gives them.
    ("{#snippet row()}\n\t<p>Plain words</p>\n{/snippet}", "Plain words"),
    ("{#snippet row(name = 'Guest user')}\n\t<p>{name}</p>\n{/snippet}", "Guest user"),
    ("{@render row('Free space')}", "Free space"),
    ("<Modal>\n\t{#snippet footer()}Close now{/snippet}\n</Modal>", "Close now"),
    ("<p {@attach tooltip('Copy the address')}>{$t('demo.title')}</p>", "Copy the address"),
    ("<Tabs items={[{ id: 'disks', name: 'Disks' }]} />", "Disks"),
    ('<ConfirmDialog confirm="Delete the share" />', "Delete the share"),
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

    @pytest.mark.parametrize("markup", [
        "<script lang=\"ts\">\n\tconst LABELS = { allow: 'demo.title', deny: 'demo.missing' };\n</script>",
        "<p>{$t({ allow: 'demo.title', deny: 'demo.missing' }[action])}</p>",
    ])
    def test_a_key_of_a_map_of_keys_must_exist(self, node, tmp_path, markup):
        # A literal that starts with the name of a catalog and a dot is a key, as in a map from
        # the values of a field to the keys of their labels: $t(LABELS[value]).
        webui = make_webui(tmp_path, files={"src/routes/new.svelte": markup + "\n"})

        result = run_check(node, webui)

        assert result.returncode == 1
        reported = [line for line in result.stderr.splitlines() if line.startswith("src/routes/new.svelte:")]
        assert len(reported) == 1, result.stderr
        assert reported[0].endswith(": 'demo.missing': no such key in the catalogs"), reported

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


    @pytest.mark.parametrize(("module", "reported"), [
        ("export const LABELS = { allow: 'demo.title', deny: 'demo.missing' };\n", "'demo.missing': no such key"),
        ("export const label = (t: (key: string) => string) => t(`demo.missing`);\n", "'demo.missing': no such key"),
        ("export const count = (t: (key: string) => string) => t('demo.files');\n",
         "t('demo.files'): a text with plural forms needs its count"),
        ("export const count = (t: (key: string, p?: object) => string) => t('demo.files', { n: 2 });\n", None),
        ("export const known = 'demo.title';\n// demo.missing in a comment is no key\n", None),
    ], ids=["map", "template", "plural", "plural-with-count", "existing"])
    def test_the_keys_of_a_ts_module_must_exist(self, node, tmp_path, module, reported):
        # The helpers of an area take $t and name its keys in .ts modules (ADR-0427).
        webui = make_webui(tmp_path, files={"src/lib/components/demo/labels.ts": module})

        result = run_check(node, webui)

        assert (result.returncode == 1) is (reported is not None), result.stderr
        if reported:
            assert "src/lib/components/demo/labels.ts:1:" in result.stderr and reported in result.stderr

    def test_the_keys_of_the_catalogs_themselves_are_not_read_as_uses(self, node, tmp_path):
        result = run_check(node, make_webui(tmp_path))
        assert result.returncode == 0, result.stderr


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
        # The areas the first part of the feature translated (ADR-0287); others join them.
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

    def test_dates_take_the_browsers_format_when_it_is_of_the_language_else_day_first(self, lookup):
        # (language of the page, navigator.language): the locale of $intlLocale. A browser of
        # another language gets day first and a 24-hour clock in the page's language: en-GB, ru.
        cases = [("en", "en-US"), ("en", "en-GB"), ("en", "en"), ("en", "ru-RU"), ("en", "de-DE"), ("en", ""),
                 ("ru", "ru-RU"), ("ru", "ru"), ("ru", "en-US"), ("ru", "de-DE"), ("ru", "")]

        locales = lookup(*[["dateLocale", page, browser] for page, browser in cases])

        assert locales == ["en-US", "en-GB", "en", "en-GB", "en-GB", "en-GB", "ru-RU", "ru", "ru", "ru", "ru"]


def test_the_check_reads_scripts_with_typescript(node, tmp_path):
    # A <script lang="ts"> with types, generics and a module script parses: Svelte's parser
    # reads only the markup.
    script = textwrap.dedent("""\
        <script module lang="ts">
        	export type Row = { id: number };
        </script>
        <script lang="ts">
        	import type { Writable } from 'svelte/store';
        	let { rows = [] }: { rows?: Array<Row> } = $props();
        	let store: Writable<number> | null = null;
        	const pick = <T,>(items: T[]): T | undefined => items[0];
        </script>
        <p>{rows.length}</p>
        """)
    webui = make_webui(tmp_path, files={"src/routes/typed.svelte": script})

    result = run_check(node, webui)

    assert result.returncode == 0, result.stderr
