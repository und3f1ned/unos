"""Pages use the design system: a check of webui/src fails for raw controls and raw colours (ADR-0400).

webui/scripts/design-check.mjs reads the .svelte files of src/ outside src/lib/components/ui with
Svelte's and TypeScript's parsers. It fails for a raw <button>, an <a> styled as a button, a text
field, select or textarea without a `form-*` class, and a raw Tailwind palette colour (bg-slate-700,
hover:text-red-400) in a class attribute, a `class:` directive or a string literal; a comment
`design-ignore: <reason>` exempts one use. Each test builds a small web UI in tmp_path and checks
it, and the real one passes.

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
CHECK = WEBUI / "scripts" / "design-check.mjs"


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
        [node, str(CHECK), *([str(webui)] if webui else [])], capture_output=True, text=True, timeout=120
    )


def demo(tmp_path: Path, files: dict[str, str]) -> Path:
    """A web UI in tmp_path with the .svelte ``files`` (paths below src/)."""
    webui = tmp_path / "webui"
    for name, text in files.items():
        path = webui / "src" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
    return webui


def check(node: str, tmp_path: Path, markup: str, script: str = "", *, name: str = "routes/a/+page.svelte"):
    body = f'<script lang="ts">\n{textwrap.dedent(script)}</script>\n\n' if script else ""
    return run_check(node, demo(tmp_path, {name: body + textwrap.dedent(markup)}))


class TestRawControls:
    def test_a_raw_button_fails_with_the_file_and_line(self, node, tmp_path):
        result = check(node, tmp_path, '<div>\n  <button on:click={go}>Go</button>\n</div>\n')
        assert result.returncode == 1
        assert "src/routes/a/+page.svelte:2:3: raw <button>" in result.stderr
        assert "Button" in result.stderr

    def test_the_components_and_the_ui_folder_pass(self, node, tmp_path):
        webui = demo(
            tmp_path,
            {
                "routes/a/+page.svelte": '<script>import { Button } from "$lib/components/ui";</script>\n<Button>Go</Button>\n',
                "lib/components/ui/Button.svelte": '<button class="bg-slate-700 text-red-400"><slot /></button>\n',
            },
        )
        result = run_check(node, webui)
        assert result.returncode == 0, result.stderr
        assert "1 .svelte files" in result.stdout

    def test_a_link_styled_as_a_button_fails(self, node, tmp_path):
        result = check(node, tmp_path, '<a href="/x" class="btn btn-secondary">Open</a>\n<a href="/y" class="text-info">Plain</a>\n')
        assert result.returncode == 1
        assert result.stderr.count("raw <a> styled as a button") == 1
        assert "+page.svelte:1:1" in result.stderr

    def test_a_text_field_without_a_form_class_fails(self, node, tmp_path):
        result = check(
            node,
            tmp_path,
            """\
            <input type="text" bind:value={a} class="w-full" />
            <input bind:value={b} />
            <select bind:value={c}></select>
            <textarea bind:value={d}></textarea>
            """,
            "let a = '', b = '', c = '', d = '';\n",
        )
        assert result.returncode == 1
        assert result.stderr.count("raw <input>") == 2
        assert "raw <select>" in result.stderr and "raw <textarea>" in result.stderr

    def test_fields_with_a_form_class_and_other_input_types_pass(self, node, tmp_path):
        result = check(
            node,
            tmp_path,
            """\
            <input type="text" class="form-input pr-10" />
            <input type="password" class="form-input" />
            <select class="form-select"></select>
            <textarea class="form-input"></textarea>
            <input type="checkbox" />
            <input type="radio" />
            <input type="file" class="hidden" />
            """,
        )
        assert result.returncode == 0, result.stderr


class TestRawColors:
    @pytest.mark.parametrize(
        "markup,found,use",
        [
            ('<div class="p-4 bg-slate-700 rounded"></div>', "bg-slate-700", "bg-surface-overlay"),
            ('<p class="text-slate-400">x</p>', "text-slate-400", "text-content-secondary"),
            ('<p class="hover:text-red-400 md:bg-green-500/20">x</p>', "hover:text-red-400", "text-status-error"),
            ('<div class="border border-slate-700/50"></div>', "border-slate-700/50", "border-border"),
            ('<div class:bg-blue-600={on}></div>', "bg-blue-600", "bg-brand"),
            ('<div class={on ? "ring-2 ring-white" : ""}></div>', "ring-white", "a token"),
            ('<div class="a {on ? `text-yellow-400` : ``}"></div>', "text-yellow-400", "text-status-warning"),
            ('<X class="w-4 h-4 text-purple-400" />', "text-purple-400", "text-accent-memory"),
        ],
    )
    def test_a_raw_colour_in_the_markup_fails_and_names_its_token(self, node, tmp_path, markup, found, use):
        result = check(node, tmp_path, markup + "\n", "let on = true;\n")
        assert result.returncode == 1, result.stdout
        assert f"raw colour class '{found}'" in result.stderr
        assert use in result.stderr

    def test_a_raw_colour_in_a_script_literal_fails(self, node, tmp_path):
        result = check(
            node,
            tmp_path,
            "<p class={tone}>x</p>\n",
            "const tone = ok ? 'text-green-400' : 'text-red-400';\nlet ok = true;\n",
        )
        assert result.returncode == 1
        assert "'text-green-400'" in result.stderr and "'text-red-400'" in result.stderr
        assert "+page.svelte:2:" in result.stderr

    def test_tokens_and_unrelated_words_pass(self, node, tmp_path):
        result = check(
            node,
            tmp_path,
            '<div class="bg-surface-overlay text-content-secondary border-border hover:bg-surface-hover '
            'bg-status-error/20 text-brand ring-brand-ring scrim"></div>\n<p>bg-slate-700 in a text</p>\n',
            "// text-slate-400 in a comment\nconst label = 'slate-700';\n",
        )
        assert result.returncode == 0, result.stderr

    def test_the_redesigns_variants_and_tokens_pass(self, node, tmp_path):
        """webui.redesign (ADR-0428): the ghost-danger Button, a text link and a stat tile's chip
        are tokens and variants, not raw colours."""
        result = check(
            node,
            tmp_path,
            '<Button variant="ghost-danger" size="icon-sm" aria-label={label}>x</Button>\n'
            '<a href="/storage" class="link text-content-link">Manage</a>\n'
            '<span class="stat-chip chip-cpu bg-accent-cpu/20 shadow-raised rounded-card"></span>\n',
            "import { Button } from '$lib/components/ui';\nconst label = 'Delete';\n",
        )
        assert result.returncode == 0, result.stderr

    def test_a_comment_with_a_reason_exempts_markup_and_script(self, node, tmp_path):
        result = check(
            node,
            tmp_path,
            """\
            <!-- design-ignore: a diagram palette that no token covers -->
            <div class="bg-slate-700"></div>
            <!-- design-ignore: the browser's own button -->
            <button>Go</button>
            <p class={other}>x</p>
            """,
            """\
            // design-ignore: matches a third-party widget
            const tone = 'text-green-400';
            const other = 'p-1'; // design-ignore: same
            """,
        )
        assert result.returncode == 0, result.stderr
        assert "3 use(s) exempted" in result.stdout

    def test_a_comment_without_a_reason_exempts_nothing(self, node, tmp_path):
        result = check(node, tmp_path, '<!-- design-ignore: -->\n<div class="bg-slate-700"></div>\n')
        assert result.returncode == 1

    def test_an_exemption_covers_only_the_next_element(self, node, tmp_path):
        result = check(
            node,
            tmp_path,
            '<!-- design-ignore: legacy swatch -->\n<div class="bg-slate-700"></div>\n<div class="bg-slate-800"></div>\n',
        )
        assert result.returncode == 1
        assert "bg-slate-800" in result.stderr and "bg-slate-700" not in result.stderr

    def test_the_table_names_only_tokens_of_the_config(self, node, tmp_path):
        webui = demo(tmp_path, {"routes/a/+page.svelte": "<p>x</p>\n"})
        (webui / "tailwind.config.js").write_text(
            "export default { theme: { extend: { colors: { surface: { DEFAULT: '#000' } } } } };\n"
        )
        result = run_check(node, webui)
        assert result.returncode == 1
        assert "lacks" in result.stderr


def test_make_frontend_runs_the_check():
    package = json.loads((WEBUI / "package.json").read_text(encoding="utf-8"))
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")

    assert package["scripts"]["design"] == "node scripts/design-check.mjs"
    assert "frontend-design frontend-build" in makefile
    assert "frontend-design:\n\tcd webui && $(NPM_BIN) run design\n" in makefile


def test_the_real_web_ui_passes(node):
    result = run_check(node)
    assert result.returncode == 0, result.stderr
