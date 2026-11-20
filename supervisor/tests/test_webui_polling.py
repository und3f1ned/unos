"""No page polls the API: a check of webui/src fails for a setInterval that reaches it (ADR-0006, ADR-0365).

webui/scripts/polling-check.mjs reads the <script> blocks of the .svelte files and the .ts modules
of src/ with TypeScript's parser. A setInterval fails when its callback reaches fetch() or what a
file imports from src/lib/api/, in its body or through the functions of its file that it names;
a comment `polling-ignore: <reason>` above its statement exempts it. Each test builds a small web
UI in tmp_path and checks it, and the real one passes.

Without node, or without webui/node_modules (make setup), the tests skip.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WEBUI = REPO / "webui"
CHECK = WEBUI / "scripts" / "polling-check.mjs"


@pytest.fixture(scope="module")
def node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed (Node.js 22, webui/package.json)")
    if not (WEBUI / "node_modules" / "typescript" / "package.json").exists():
        pytest.skip("webui/node_modules has no typescript: make setup installs it")
    return node


def run_check(node: str, webui: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [node, str(CHECK), *([str(webui)] if webui else [])], capture_output=True, text=True, timeout=120
    )


API_MODULE = """\
export const api = { get: (path: string) => fetch(path) };
export async function getStatus(): Promise<string> { return 'idle'; }
export async function getEntries(): Promise<string[]> { return []; }
"""


def demo(tmp_path: Path, files: dict[str, str]) -> Path:
    """A web UI in tmp_path with src/lib/api/demo.ts and ``files`` (paths below src/)."""
    webui = tmp_path / "webui"
    (webui / "src" / "lib" / "api").mkdir(parents=True)
    (webui / "src" / "lib" / "api" / "demo.ts").write_text(API_MODULE)
    for name, text in files.items():
        path = webui / "src" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
    return webui


def page(script: str) -> str:
    return f'<script lang="ts">\n{textwrap.dedent(script)}</script>\n\n<p>{{status}}</p>\n'


IMPORTS = "import { getStatus, getEntries, api } from '$lib/api/demo';\n"


def check(node: str, tmp_path: Path, script: str, *, name: str = "routes/a/+page.svelte") -> subprocess.CompletedProcess:
    return run_check(node, demo(tmp_path, {name: page(IMPORTS + script)}))


class TestPolling:
    def test_a_callback_that_calls_the_api_fails_with_the_file_line_and_function(self, node, tmp_path):
        result = check(node, tmp_path, """\
            let status = '';
            setInterval(async () => {
              status = await getStatus();
            }, 2000);
            """)
        assert result.returncode == 1
        assert "src/routes/a/+page.svelte:4: setInterval reaches the API: getStatus (from $lib/api/demo)" in result.stderr

    def test_the_functions_of_the_file_that_a_callback_names_are_followed(self, node, tmp_path):
        result = check(node, tmp_path, """\
            let entries: string[] = [];
            let loading = false;
            let timer: ReturnType<typeof setInterval> | null = null;
            async function load() { loading = true; await refresh(); loading = false; }
            const refresh = async () => { entries = await getEntries(); };
            function toggle() {
              timer = setInterval(() => { if (!loading) load(); }, 5000);
            }
            """)
        assert result.returncode == 1
        assert ("+page.svelte:9: setInterval reaches the API: getEntries (from $lib/api/demo) through load -> refresh"
                in result.stderr)

    def test_a_callback_given_by_name_is_followed(self, node, tmp_path):
        result = check(node, tmp_path, """\
            let status = $state('');
            const tick = async () => { status = await getStatus(); };
            setInterval(tick, 1000);
            """)
        assert result.returncode == 1
        assert "setInterval reaches the API: getStatus (from $lib/api/demo) through tick" in result.stderr

    @pytest.mark.parametrize("body, found", [
        ("api.get('/system/info')", "api (from $lib/api/demo)"),
        ("fetch('/api/v1/system/info')", "fetch (from the browser)"),
        ("['a', 'b'].forEach((pool) => getStatus())", "getStatus (from $lib/api/demo)"),
        ("window.setTimeout(getStatus, 0)", "getStatus (from $lib/api/demo)"),
    ])
    def test_every_use_of_the_api_counts(self, node, tmp_path, body, found):
        result = check(node, tmp_path, f"setInterval(() => {{ {body}; }}, 2000);\n")
        assert result.returncode == 1
        assert f"setInterval reaches the API: {found}" in result.stderr

    def test_an_api_function_given_as_the_callback_fails(self, node, tmp_path):
        result = check(node, tmp_path, "setInterval(getStatus, 2000);\n")
        assert result.returncode == 1
        assert "setInterval reaches the API: getStatus (from $lib/api/demo)" in result.stderr

    def test_a_relative_import_of_the_api_counts(self, node, tmp_path):
        script = "import { getStatus } from '../../lib/api/demo';\nsetInterval(() => getStatus(), 2000);\n"
        result = run_check(node, demo(tmp_path, {"routes/a/+page.svelte": page(script)}))
        assert result.returncode == 1
        assert "setInterval reaches the API: getStatus (from ../../lib/api/demo)" in result.stderr

    def test_a_ts_module_is_checked_too(self, node, tmp_path):
        module = "import { getStatus } from './api/demo';\nexport function poll() { return setInterval(getStatus, 2000); }\n"
        result = run_check(node, demo(tmp_path, {"lib/poller.ts": module}))
        assert result.returncode == 1
        assert "src/lib/poller.ts:2: setInterval reaches the API: getStatus (from ./api/demo)" in result.stderr

    def test_a_callback_the_check_cannot_see_into_fails(self, node, tmp_path):
        result = check(node, tmp_path, "const timers = { tick() {} };\nsetInterval(timers.tick, 1000);\n")
        assert result.returncode == 1
        assert "setInterval calls timers.tick, which the check cannot see into" in result.stderr


POLLER = """\
import { getStatus } from './api/demo';
export async function refresh() { return getStatus(); }
export function format(n: number) { return String(n); }
"""

FEED = "import { writable } from 'svelte/store';\nexport const feed = writable<string[]>([]);\n"


class TestOtherModules:
    """A function that a .ts module of src/ exports, and that reaches the API, is API too."""

    @pytest.mark.parametrize("module, imported, found", [
        (POLLER, "{ refresh }", "refresh (from $lib/poller, which reaches getStatus (from ./api/demo))"),
        ("import { api } from '$lib/api/demo';\nexport const refresh = () => api.get('/x');\n", "{ refresh }",
         "refresh (from $lib/poller, which reaches api (from $lib/api/demo))"),
        ("import { getStatus } from './api/demo';\nfunction load() { return getStatus(); }\nexport { load as refresh };\n",
         "{ refresh }", "refresh (from $lib/poller, which reaches getStatus (from ./api/demo) through load)"),
        ("export { getStatus as refresh } from './api/demo';\n", "{ refresh }",
         "refresh (from $lib/poller, which reaches getStatus (from ./api/demo))"),
        ("export async function refresh() { return fetch('/api/v1/x'); }\n", "{ refresh }",
         "refresh (from $lib/poller, which reaches fetch (from the browser))"),
        ("import { getStatus } from './api/demo';\nexport default function () { return getStatus(); }\n", "refresh",
         "refresh (from $lib/poller, which reaches getStatus (from ./api/demo))"),
        (POLLER, "* as refresh", "refresh (from $lib/poller, which reaches getStatus (from ./api/demo))"),
    ], ids=["function", "arrow-function", "export-list", "re-export", "fetch", "default", "namespace"])
    def test_an_exported_function_that_reaches_the_api_counts(self, node, tmp_path, module, imported, found):
        script = f"import {imported} from '$lib/poller';\nsetInterval(() => {{ refresh; }}, 2000);\n"
        result = run_check(node, demo(tmp_path, {"lib/poller.ts": module, "routes/a/+page.svelte": page(script)}))
        assert result.returncode == 1
        assert f"src/routes/a/+page.svelte:3: setInterval reaches the API: {found}" in result.stderr

    def test_modules_are_followed_through_each_other(self, node, tmp_path):
        feed = "import { refresh } from '../poller';\nexport function follow() { refresh(); }\nexport * from '../poller';\n"
        script = ("import { follow, refresh as again } from '$lib/stores/feed';\n"
                  "setInterval(() => follow(), 2000);\nsetInterval(again, 2000);\n")
        webui = demo(tmp_path, {"lib/poller.ts": POLLER, "lib/stores/feed.ts": feed,
                                "routes/a/+page.svelte": page(script)})
        result = run_check(node, webui)
        assert result.returncode == 1
        assert ("+page.svelte:3: setInterval reaches the API: follow (from $lib/stores/feed, which reaches refresh "
                "(from ../poller, which reaches getStatus (from ./api/demo)))") in result.stderr
        assert ("+page.svelte:4: setInterval reaches the API: again (from $lib/stores/feed, which reaches getStatus "
                "(from ./api/demo))") in result.stderr

    def test_a_ts_module_follows_the_modules_it_imports(self, node, tmp_path):
        module = "import { refresh } from './poller';\nexport function poll() { return setInterval(() => refresh(), 2000); }\n"
        result = run_check(node, demo(tmp_path, {"lib/poller.ts": POLLER, "lib/timer.ts": module}))
        assert result.returncode == 1
        assert "src/lib/timer.ts:2: setInterval reaches the API: refresh (from ./poller, which reaches" in result.stderr

    def test_what_a_module_exports_without_reaching_the_api_passes(self, node, tmp_path):
        cycle = "import { format } from './poller';\nexport function other() { return format(1); }\n"
        script = """\
            import { format } from '$lib/poller';
            import { other } from '$lib/cycle';
            let shown = '';
            setInterval(() => { shown = format(Date.now()) + other(); }, 1000);
            """
        webui = demo(tmp_path, {"lib/poller.ts": POLLER + "import { other } from './cycle';\n", "lib/cycle.ts": cycle,
                                "routes/a/+page.svelte": page(script)})
        result = run_check(node, webui)
        assert result.returncode == 0, result.stderr


class TestWhatATimerChanges:
    """A timer that changes what an API-reaching `$effect` reads, or a store, polls through it."""

    @pytest.mark.parametrize("bump, through", [
        ("tick += 1", ""), ("tick = tick + 1", ""), ("tick++", ""), ("bump()", " through bump"),
        ("[tick] = [tick + 1]", ""), ("counter.tick = 1", ""),
    ])
    @pytest.mark.parametrize("rune", ["$effect", "$effect.pre"])
    def test_a_variable_that_an_api_reaching_effect_reads(self, node, tmp_path, bump, through, rune):
        changed = "counter" if bump.startswith("counter") else "tick"
        result = check(node, tmp_path, f"""\
            let tick = $state(0);
            let counter = $state({{ tick: 0 }});
            let status = $state('');
            async function load() {{ status = await getStatus(); }}
            function bump() {{ tick += 1; }}
            {rune}(() => {{ tick; counter; load(); }});
            setInterval(() => {{ {bump}; }}, 5000);
            """)
        assert result.returncode == 1
        assert (f"+page.svelte:9: setInterval changes {changed}{through}, which the {rune} of line 8 reads; "
                "it reaches the API: getStatus (from $lib/api/demo) through load") in result.stderr

    def test_what_the_functions_that_an_effect_calls_read_counts(self, node, tmp_path):
        # Svelte 5 tracks every read of a run, also in the functions it calls.
        result = check(node, tmp_path, """\
            let tick = $state(0);
            function refresh() { if (tick > 0) getStatus(); }
            $effect(() => { refresh(); });
            setInterval(() => { tick += 1; }, 5000);
            """)
        assert result.returncode == 1
        assert ("+page.svelte:6: setInterval changes tick, which the $effect of line 5 reads; "
                "it reaches the API: getStatus (from $lib/api/demo) through refresh") in result.stderr

    def test_what_an_effect_reads_untracked_does_not_count(self, node, tmp_path):
        script = """\
            import { untrack } from 'svelte';
            let tick = $state(0);
            let pool = $state('tank');
            $effect(() => { const chosen = pool; untrack(() => { if (tick > 0) getStatus(); }); });
            setInterval(() => { {changed} = {value}; }, 5000);
            """
        passes = check(node, tmp_path / "tick", script.replace("{changed}", "tick").replace("{value}", "1"))
        assert passes.returncode == 0, passes.stderr
        fails = check(node, tmp_path / "pool", script.replace("{changed}", "pool").replace("{value}", "'other'"))
        assert fails.returncode == 1
        assert "setInterval changes pool, which the $effect of line 6 reads; it reaches the API" in fails.stderr

    @pytest.mark.parametrize("derived", ["$derived(Math.floor(now / 60000))", "$derived.by(() => Math.floor(now / 60000))"])
    def test_a_variable_read_through_a_derived_value(self, node, tmp_path, derived):
        result = check(node, tmp_path, f"""\
            let now = $state(Date.now());
            const minute = {derived};
            const run = () => getStatus();
            $effect(() => {{ if (minute >= 0) run(); }});
            setInterval(() => {{ now = Date.now(); }}, 1000);
            """)
        assert result.returncode == 1
        assert ("+page.svelte:7: setInterval changes now, which the $effect of line 6 reads through minute; "
                "it reaches the API: getStatus (from $lib/api/demo) through run") in result.stderr

    def test_a_variable_that_an_effect_reads_and_assigns_counts(self, node, tmp_path):
        # Svelte 5 runs an effect again when a variable that it read changes, also one it assigns.
        result = check(node, tmp_path, """\
            let page = $state(1);
            $effect(() => { if (page < 1) page = 1; getEntries(); });
            setInterval(() => { page++; }, 5000);
            """)
        assert result.returncode == 1
        assert "+page.svelte:5: setInterval changes page, which the $effect of line 4 reads" in result.stderr

    def test_a_derived_value_that_reaches_the_api_counts(self, node, tmp_path):
        result = check(node, tmp_path, """\
            let tick = $state(0);
            const status = $derived(getStatus().then((s) => s + tick));
            setInterval(() => { tick++; }, 5000);
            """)
        assert result.returncode == 1
        assert "+page.svelte:5: setInterval changes tick, which the $derived of line 4 reads" in result.stderr

    @pytest.mark.parametrize("effect", [
        "function refresh() { if (tick > 0) getStatus(); }\n$effect(refresh);",
        "if (true) { $effect(() => { if (tick > 0) getStatus(); }); }",
        "function follow() { $effect(() => { if (tick > 0) getStatus(); }); }\nfollow();",
    ], ids=["by-name", "in-a-block", "in-a-function"])
    def test_an_effect_anywhere_in_the_script_counts(self, node, tmp_path, effect):
        result = check(node, tmp_path, "let tick = $state(0);\n" + effect + "\nsetInterval(() => { tick++; }, 5000);\n")
        assert result.returncode == 1
        assert "setInterval changes tick, which the $effect of line" in result.stderr

    def test_a_method_of_a_class_that_reaches_the_api_counts(self, node, tmp_path):
        # The state of a page in a .svelte.ts module (ADR-0427): a timer that calls a method of it
        # that reaches the API, directly or through another method, polls.
        module = """\
            import { getStatus } from '$lib/api/demo';
            export class Lists {
              status = $state('');
              async load() { this.status = await getStatus(); }
              reload = () => this.load();
              count() { return 1; }
            }
            """
        script = """\
            import { Lists } from '$lib/components/demo/lists.svelte';
            const lists = new Lists();
            setInterval(() => { {call}; }, 5000);
            """
        for call, found in [("lists.load()", "lists.load"), ("void lists.reload()", "lists.reload")]:
            webui = demo(tmp_path / found, {"lib/components/demo/lists.svelte.ts": module,
                                            "routes/a/+page.svelte": page(script.replace("{call}", call))})
            result = run_check(node, webui)
            assert result.returncode == 1, result.stdout
            assert f"setInterval reaches the API: {found} (from src/lib/components/demo/lists.svelte.ts" in result.stderr
        webui = demo(tmp_path / "count", {"lib/components/demo/lists.svelte.ts": module,
                                          "routes/a/+page.svelte": page(script.replace("{call}", "lists.count()"))})
        assert run_check(node, webui).returncode == 0

    @pytest.mark.parametrize("script, store", [
        ("import { writable } from 'svelte/store';\nconst tick = writable(0);\n"
         "setInterval(() => tick.update((n) => n + 1), 1000);\n", "tick"),
        ("import { writable } from 'svelte/store';\nconst tick = writable(0);\n"
         "setInterval(() => { $tick = Date.now(); }, 1000);\n", "tick"),
        ("import { feed } from '$lib/stores/feed';\nsetInterval(() => feed.set([]), 1000);\n", "feed"),
        ("import { custom } from '$lib/custom';\nconst shown = $derived($custom);\nsetInterval(() => custom.set(1), 1000);\n",
         "custom"),
    ], ids=["local-update", "local-dollar-assignment", "imported-writable", "read-with-a-dollar"])
    def test_a_store(self, node, tmp_path, script, store):
        files = {"lib/stores/feed.ts": FEED, "lib/custom.ts": "export const custom = { set(n: number) {} };\n",
                 "routes/a/+page.svelte": page(script)}
        result = run_check(node, demo(tmp_path, files))
        assert result.returncode == 1
        assert f"setInterval changes the store {store}: " in result.stderr

    def test_the_runes_are_no_stores(self, node, tmp_path):
        result = check(node, tmp_path, """\
            let { props = { set(n: number) {} } }: { props?: { set(n: number): void } } = $props();
            let effect = { set(n: number) {} };
            $effect(() => {});
            setInterval(() => { props.set(1); effect.set(1); }, 1000);
            """)
        assert result.returncode == 0, result.stderr

    def test_what_no_api_reaching_effect_reads_passes(self, node, tmp_path):
        result = check(node, tmp_path, """\
            let now = $state(Date.now());
            let pool = $state('tank');
            let status = $state('');
            let plain = 0;
            const cache = new Map<string, number>();
            const seconds = $derived(Math.round(now / 1000));
            const refreshPool = () => getStatus().then((s) => { status = s + now; });
            $effect(() => { if (pool) getStatus().then((s) => { status = s; }); });
            $effect(() => { if (plain > 0) getStatus(); });
            setInterval(() => { now = Date.now(); plain += 1; cache.set('now', now); }, 1000);
            """)
        assert result.returncode == 0, result.stderr


class TestWhatPasses:
    def test_a_timer_that_reaches_no_api_passes(self, node, tmp_path):
        result = check(node, tmp_path, """\
            let now = Date.now();
            let status = '';
            const timer = setInterval(() => { now = Date.now(); }, 1000);
            async function load() { status = await getStatus(); }
            load();
            """)
        assert result.returncode == 0, result.stderr
        assert "1 setInterval call(s), none reaches the API (0 exempted with polling-ignore)" in result.stdout

    def test_a_timer_with_a_reason_to_reach_the_api_is_exempted(self, node, tmp_path):
        result = check(node, tmp_path, """\
            let deadline = Date.now() + 60000;
            // polling-ignore: a countdown; the status is read once when its time has run out.
            setInterval(() => { if (Date.now() > deadline) getStatus(); }, 1000);
            """)
        assert result.returncode == 0, result.stderr
        assert "(1 exempted with polling-ignore)" in result.stdout

    def test_an_exemption_needs_its_reason(self, node, tmp_path):
        result = check(node, tmp_path, "// polling-ignore:\nsetInterval(() => getStatus(), 1000);\n")
        assert result.returncode == 1

    def test_types_and_type_only_imports_are_no_api_use(self, node, tmp_path):
        script = """\
            import type { Entry } from '$lib/api/demo';
            import { type Status } from '$lib/api/demo';
            let shown: Entry[] = [];
            setInterval(() => { const next: Status | null = null; shown = [...shown]; }, 1000);
            """
        result = run_check(node, demo(tmp_path, {"routes/a/+page.svelte": page(script)}))
        assert result.returncode == 0, result.stderr


def test_the_web_ui_polls_no_api(node):
    result = run_check(node)
    assert result.returncode == 0, result.stderr
    assert "none reaches the API" in result.stdout
