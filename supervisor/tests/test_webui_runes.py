"""Every component of the web UI is a Svelte 5 component in runes mode, none in legacy mode (ADR-0425).

webui/scripts/runes-check.mjs compiles each .svelte file of src/ with Svelte's compiler in runes
mode, as svelte.config.js builds it, and fails for a component that is not in runes mode after all
(`<svelte:options runes={false} />`), for what runes mode refuses (`export let`, `$:`, `$$props`,
`$$restProps`, beforeUpdate) and for the syntax of Svelte 4 that it still accepts: `on:` and `let:`
directives, `<slot>`, a `slot` attribute, `<svelte:fragment>`, `<svelte:component>`,
`<svelte:self>`, `<script context="module">`, `$$slots`, the options accessors and immutable, a
plain `let` that the markup shows and that changes, and the imports of svelte/legacy,
createEventDispatcher and $app/stores, in components and in .ts modules. `make frontend` runs it
(`npm run runes`). Each test builds a small web UI in tmp_path and checks it, and the real one
passes.

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
CHECK = WEBUI / "scripts" / "runes-check.mjs"


@pytest.fixture(scope="module")
def node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed (Node.js 22, webui/package.json)")
    if not (WEBUI / "node_modules" / "svelte" / "package.json").exists():
        pytest.skip("webui/node_modules has no svelte: make setup installs it")
    return node


def run_check(node: str, webui: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [node, str(CHECK), *([str(webui)] if webui else [])], capture_output=True, text=True, timeout=300
    )


def check(node: str, tmp_path: Path, component: str, name: str = "lib/components/Demo.svelte") -> subprocess.CompletedProcess:
    webui = tmp_path / "webui"
    path = webui / "src" / name
    path.parent.mkdir(parents=True)
    path.write_text(textwrap.dedent(component))
    return run_check(node, webui)


LEGACY = '<script lang="ts">\n\texport let label = "";\n\t$: shout = label.toUpperCase();\n</script>\n<p>{shout}</p>\n'


RUNES = """\
    <script lang="ts">
    \timport type { Snippet } from 'svelte';
    \tlet { label, children, onpick }: { label: string; children?: Snippet; onpick?: () => void } = $props();
    \tlet count = $state(0);
    \tconst double = $derived(count * 2);
    </script>

    <button onclick={() => { count += 1; onpick?.(); }}>{label} {double}</button>
    {@render children?.()}
    """


def test_a_component_in_runes_mode_passes(node, tmp_path):
    result = check(node, tmp_path, RUNES)
    assert result.returncode == 0, result.stderr
    assert "1 .svelte files" in result.stdout


@pytest.mark.parametrize(
    ("component", "found"),
    [
        ('<script lang="ts">\n\texport let label = "";\n</script>\n<p>{label}</p>\n', "export let"),
        ('<script lang="ts">\n\tlet n = 1;\n\t$: double = n * 2;\n</script>\n<p>{double}</p>\n', "$:"),
        ("<p>{$$props.title}</p>\n", "$$props"),
        ('<script lang="ts">\n\tlet n = $state(0);\n</script>\n<button on:click={() => n++}>{n}</button>\n', "an on: directive"),
        ("<div><slot /></div>\n", "<slot>"),
        ('<script lang="ts">\n\timport Card from "./Card.svelte";\n</script>\n<Card><p slot="footer">x</p></Card>\n', "slot attribute"),
        ('<script lang="ts">\n\timport Card from "./Card.svelte";\n</script>\n'
         '<Card><svelte:fragment slot="footer">x</svelte:fragment></Card>\n', "<svelte:fragment>"),
        ('<script lang="ts">\n\tlet { icon } = $props();\n</script>\n<svelte:component this={icon} />\n', "<svelte:component>"),
        ('<script context="module" lang="ts">\n\texport const n = 1;\n</script>\n<p>{n}</p>\n', 'context="module"'),
        ('<script lang="ts">\n\timport { run } from "svelte/legacy";\n\tlet n = $state(0);\n\trun(() => n);\n</script>\n', "svelte/legacy"),
        ('<script lang="ts">\n\timport { createEventDispatcher } from "svelte";\n\tconst dispatch = createEventDispatcher();\n'
         '\tlet { x } = $props();\n</script>\n<p>{x}</p>\n', "createEventDispatcher"),
        ('<script lang="ts">\n\timport { page } from "$app/stores";\n</script>\n<p>{$page.url.pathname}</p>\n', "$app/stores"),
        ("<svelte:options runes={false} />\n" + LEGACY, "not in runes mode"),
        ("<p>{$$restProps.title}</p>\n", "$$restProps"),
        ("{#if $$slots.footer}<p>x</p>{/if}\n", "$$slots"),
        ('<script lang="ts">\n\timport { beforeUpdate } from "svelte";\n\tbeforeUpdate(() => {});\n</script>\n', "beforeUpdate"),
        ("{#if true}<svelte:self />{/if}\n", "<svelte:self>"),
        ('<script lang="ts">\n\timport Card from "./Card.svelte";\n</script>\n<Card on:close={() => {}} />\n', "an on: directive"),
        ('<script lang="ts">\n\timport List from "./List.svelte";\n</script>\n<List let:item>{item}</List>\n', "let: directive"),
        ("<svelte:options accessors />\n<p>x</p>\n", "accessors"),
        ('<script lang="ts">\n\tlet n = 0;\n</script>\n<button onclick={() => n++}>{n}</button>\n', "$state"),
        ('<script lang="ts">\n\tconst stores = import("$app/stores");\n</script>\n', "$app/stores"),
    ],
)
def test_the_syntax_of_svelte_4_fails_with_the_file_and_line(node, tmp_path, component, found):
    result = check(node, tmp_path, component)
    assert result.returncode == 1, result.stdout
    assert "src/lib/components/Demo.svelte:" in result.stderr
    assert found in result.stderr


def test_a_component_that_cannot_be_compiled_fails(node, tmp_path):
    result = check(node, tmp_path, "<p>{#if}</p>\n")
    assert result.returncode == 1
    assert "src/lib/components/Demo.svelte:" in result.stderr


@pytest.mark.parametrize("module", ["lib/stores/demo.ts", "lib/components/demo.svelte.ts"])
@pytest.mark.parametrize(("imported", "found"), [
    ("import { run } from 'svelte/legacy';\n", "svelte/legacy"),
    ("export const page = import('$app/stores');\n", "$app/stores"),
])
def test_a_ts_module_fails_for_the_imports_of_svelte_4(node, tmp_path, module, imported, found):
    webui = tmp_path / "webui"
    (webui / "src" / module).parent.mkdir(parents=True)
    (webui / "src" / module).write_text(imported)
    result = run_check(node, webui)
    assert result.returncode == 1
    assert f"src/{module}:1:" in result.stderr and found in result.stderr


def test_the_svelte_config_compiles_the_web_ui_in_runes_mode(node):
    # The build refuses a component of legacy mode too: every file of src/ is compiled with
    # runes: true; only the libraries under node_modules (lucide-svelte 0.x) keep their mode.
    script = (
        "const { default: config } = await import(process.argv[1]);"
        "const options = config.vitePlugin.dynamicCompileOptions;"
        "console.log(JSON.stringify([options({ filename: process.argv[2] }), options({ filename: process.argv[3] })]));"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", script, (WEBUI / "svelte.config.js").as_uri(),
         str(WEBUI / "src" / "routes" / "+page.svelte"), str(WEBUI / "node_modules" / "lucide-svelte" / "dist" / "Icon.svelte")],
        capture_output=True, text=True, timeout=60, cwd=WEBUI,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '[{"runes":true},null]'


def test_make_frontend_runs_the_runes_check():
    package = (WEBUI / "package.json").read_text()
    makefile = (REPO / "Makefile").read_text()
    assert '"runes": "node scripts/runes-check.mjs"' in package
    assert "frontend-check frontend-runes frontend-i18n" in makefile
    assert "frontend-runes:\n\tcd webui && $(NPM_BIN) run runes\n" in makefile


def test_the_web_ui_has_no_component_of_legacy_mode(node):
    result = run_check(node)
    assert result.returncode == 0, result.stderr
    assert "runes mode" in result.stdout
