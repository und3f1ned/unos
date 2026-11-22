"""Pages use the design system: a check of webui/src fails for raw controls and raw colours (ADR-0400).

webui/scripts/design-check.mjs reads the .svelte files of src/ outside src/lib/components/ui with
Svelte's and TypeScript's parsers. It fails for a raw <button>, an <a> styled as a button, a text
field, select or textarea without a `form-*` class, and a raw Tailwind palette colour (bg-slate-700,
hover:text-red-400) in a class attribute, a `class:` directive or a string literal; and since
ADR-0432 for the browser's own dialogs (window.confirm, alert, prompt, also in a .ts module), the
class `uppercase`, a raw checkbox or radio button and a filled primary Button in a card's header or a
table's row; and since ADR-0438, which removed the baseline of file counts, for a success Alert (a
success after an action is a toast), a hand-made spinner (animate-spin, loading-spinner) and a raw
<details>. A comment `design-ignore: <reason>` exempts one use. Each test builds a small web UI in
tmp_path and checks it, and the real one passes.

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
            <input type="file" class="hidden" />
            <input type="range" />
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

    def test_the_table_names_only_tokens_of_the_theme(self, node, tmp_path):
        # The colour tokens are the --color-* variables of the @theme blocks of src/app.css
        # (Tailwind CSS 4, ADR-0450); a variable of the themes' blocks is no token.
        webui = demo(tmp_path, {"routes/a/+page.svelte": "<p>x</p>\n"})
        (webui / "src" / "app.css").write_text(
            "@theme inline reference {\n\t--color-surface: rgb(var(--color-surface));\n}\n"
            "[data-theme='dark'] {\n\t--color-content: 1 2 3;\n}\n"
        )
        result = run_check(node, webui)
        assert result.returncode == 1
        assert "the token 'content', which the @theme of src/app.css lacks" in result.stderr
        assert "the token 'surface'," not in result.stderr


class TestFoundations:
    """The rules of webui.design-foundations (ADR-0432)."""

    @pytest.mark.parametrize(
        "script,called",
        [
            ("function go() { if (!confirm('Sure?')) return; }\n", "confirm()"),
            ("function go() { window.confirm('Sure?'); }\n", "window.confirm()"),
            ("function go() { alert('Failed'); }\n", "alert()"),
            ("function go() { globalThis.prompt('Name?'); }\n", "globalThis.prompt()"),
        ],
    )
    def test_the_browsers_dialogs_fail_in_a_component(self, node, tmp_path, script, called):
        result = check(node, tmp_path, "<p>x</p>\n", script)
        assert result.returncode == 1, result.stdout
        assert f"the browser's {called}" in result.stderr
        assert "confirm() and toast of $lib/components/ui" in result.stderr

    def test_a_browser_dialog_fails_in_the_markup_and_in_a_ts_module(self, node, tmp_path):
        webui = demo(
            tmp_path,
            {
                "routes/a/+page.svelte": "<Button onclick={() => alert('x')}>Go</Button>\n",
                "lib/helpers.ts": "export function ask(): boolean {\n  return window.confirm('Sure?');\n}\n",
            },
        )
        result = run_check(node, webui)
        assert result.returncode == 1
        assert "src/routes/a/+page.svelte:1:" in result.stderr and "alert()" in result.stderr
        assert "src/lib/helpers.ts:2:10: the browser's window.confirm()" in result.stderr

    def test_the_ui_confirm_and_a_components_own_confirm_pass(self, node, tmp_path):
        webui = demo(
            tmp_path,
            {
                "routes/a/+page.svelte": (
                    '<script lang="ts">\nimport { confirm, toast } from "$lib/components/ui";\n'
                    "async function go() { if (await confirm({ title: 't', confirmLabel: 'c' })) toast.success('ok'); }\n"
                    "</script>\n<p>x</p>\n"
                ),
                "routes/b/+page.svelte": (
                    '<script lang="ts">\nasync function confirm() { return true; }\n</script>\n'
                    "<Button onclick={() => confirm()}>Go</Button>\n"
                ),
            },
        )
        result = run_check(node, webui)
        assert result.returncode == 0, result.stderr

    def test_the_uppercase_class_fails(self, node, tmp_path):
        result = check(node, tmp_path, '<span class="badge uppercase">On</span>\n<p class="sm:uppercase">x</p>\n')
        assert result.returncode == 1
        assert result.stderr.count("class 'uppercase'") == 1 and "class 'sm:uppercase'" in result.stderr
        assert "eyebrow" in result.stderr

    def test_a_raw_checkbox_or_radio_fails(self, node, tmp_path):
        result = check(node, tmp_path, '<input type="checkbox" bind:checked={a} />\n<input type="radio" />\n', "let a = $state(false);\n")
        assert result.returncode == 1
        assert "+page.svelte:5:1: raw <input type=\"checkbox\">: use Checkbox or Switch" in result.stderr
        assert "+page.svelte:6:1: raw <input type=\"radio\">: use RadioGroup" in result.stderr

    def test_no_baseline_admits_a_use(self, node, tmp_path):
        """ADR-0438: the baseline of ADR-0432 is gone; a file of counts beside the check admits nothing."""
        name = "src/routes/a/+page.svelte"
        webui = demo(tmp_path, {"routes/a/+page.svelte": '<input type="checkbox" />\n<table><tr><td><Button>Edit</Button></td></tr></table>\n'})
        (webui / "scripts").mkdir()
        (webui / "scripts" / "design-baseline.json").write_text(
            json.dumps({"raw-choice": {name: 1}, "primary-in-card": {name: 1}})
        )
        result = run_check(node, webui)
        assert result.returncode == 1
        assert "raw <input type=\"checkbox\">" in result.stderr and "a filled primary Button in a table's row" in result.stderr
        assert "baseline" not in result.stderr + result.stdout
        assert not (WEBUI / "scripts" / "design-baseline.json").exists()

    def test_the_components_choices_pass(self, node, tmp_path):
        result = check(
            node,
            tmp_path,
            '<Checkbox bind:checked={a} label="A" />\n<Switch bind:checked={a} label="B" />\n'
            '<RadioGroup legend="C" options={[]} />\n',
            "import { Checkbox, RadioGroup, Switch } from '$lib/components/ui';\nlet a = $state(false);\n",
        )
        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize(
        "markup,where",
        [
            ("<Card title=\"T\">{#snippet actions()}<Button>Add</Button>{/snippet}</Card>\n", "a card's header (Card actions)"),
            ('<div class="card-header"><h3>T</h3><Button variant="primary">Add</Button></div>\n', "a card's header"),
            ('<div class="flex justify-between"><h3 class="card-title">T</h3><Button size="sm">Add</Button></div>\n', "a card's header"),
            ("<table><tr><td><Button>Edit</Button></td></tr></table>\n", "a table's row"),
            ('<div class="table-actions"><Button>Edit</Button></div>\n', "a table's row"),
        ],
    )
    def test_a_primary_button_in_a_card_header_or_a_row_fails(self, node, tmp_path, markup, where):
        result = check(node, tmp_path, markup, "import { Button, Card } from '$lib/components/ui';\n")
        assert result.returncode == 1, result.stdout
        assert f"a filled primary Button in {where}" in result.stderr
        assert "CardAction" in result.stderr

    def test_secondary_buttons_there_and_primaries_elsewhere_pass(self, node, tmp_path):
        result = check(
            node,
            tmp_path,
            """\
            <div class="page-header"><h1 class="page-title">P</h1><Button>Create</Button></div>
            <Card title="T">{#snippet actions()}<CardAction onclick={go}>Add</CardAction>{/snippet}</Card>
            <div class="card-header"><h3>T</h3><Button variant="secondary" size="sm">Add</Button></div>
            <table><tr><td><Button variant="ghost">Edit</Button></td></tr></table>
            <Modal open title="M"><div class="card-header"><h3>S</h3></div>{#snippet footer()}<Button>Save</Button>{/snippet}</Modal>
            <form><Button type="submit">Save</Button></form>
            """,
            "import { Button, Card, CardAction, Modal } from '$lib/components/ui';\nfunction go() {}\n",
        )
        assert result.returncode == 0, result.stderr

    def test_a_comment_with_a_reason_exempts_a_choice_and_a_button(self, node, tmp_path):
        result = check(
            node,
            tmp_path,
            """\
            <!-- design-ignore: a choice card that styles its own radio -->
            <input type="radio" />
            <div class="card-header"><h3>T</h3>
              <!-- design-ignore: the page's one main action sits here -->
              <Button>Add</Button>
            </div>
            """,
            "import { Button } from '$lib/components/ui';\n",
        )
        assert result.returncode == 0, result.stderr
        assert "2 use(s) exempted" in result.stdout



class TestLockIn:
    """The rules of ADR-0438: what the pages hand-made before the shared components came back never."""

    @pytest.mark.parametrize(
        "markup",
        [
            '<Alert variant="success">Saved</Alert>\n',
            '<Alert\n  variant="success"\n  title="Done"\n>Saved</Alert>\n',
            "<Alert variant={ok ? 'success' : 'error'}>{text}</Alert>\n",
        ],
    )
    def test_a_success_alert_fails(self, node, tmp_path, markup):
        result = check(node, tmp_path, markup, "import { Alert } from '$lib/components/ui';\nlet ok = true, text = '';\n")
        assert result.returncode == 1, result.stdout
        assert "+page.svelte:6:1: a success Alert" in result.stderr
        assert "toast.success" in result.stderr and "ADR-0438" in result.stderr

    def test_other_alerts_success_badges_and_a_toast_pass(self, node, tmp_path):
        result = check(
            node,
            tmp_path,
            """\
            <Alert variant="error">{text}</Alert>
            <Alert variant={ok ? 'warning' : 'info'}>{text}</Alert>
            <Badge variant="success">On</Badge>
            <Button onclick={() => toast.success(text)}>Save</Button>
            """,
            "import { Alert, Badge, Button, toast } from '$lib/components/ui';\nlet ok = true, text = '';\n",
        )
        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize(
        "markup,script,found",
        [
            ('<Loader2 class="w-4 h-4 animate-spin" />\n', "", "animate-spin"),
            ('<span class="motion-safe:animate-spin">x</span>\n', "", "motion-safe:animate-spin"),
            ("<Loader2 class:animate-spin={busy} />\n", "", "animate-spin"),
            ('<Loader2 class="w-4 h-4 loading-spinner" />\n', "", "loading-spinner"),
            ("<Loader2 class={spin} />\n", "const spin = busy ? 'animate-spin' : '';\n", "animate-spin"),
        ],
    )
    def test_a_hand_made_spinner_fails(self, node, tmp_path, markup, script, found):
        result = check(node, tmp_path, markup, "let busy = true;\n" + script)
        assert result.returncode == 1, result.stdout
        assert f"class '{found}': a hand-made spinner" in result.stderr
        assert "Button's loading, LoadState or Skeleton" in result.stderr

    def test_a_raw_details_fails(self, node, tmp_path):
        result = check(node, tmp_path, "<p>x</p>\n<details class=\"mt-2\"><summary>More</summary><p>y</p></details>\n")
        assert result.returncode == 1
        assert "+page.svelte:2:1: raw <details>: use TechDetails" in result.stderr

    def test_the_components_and_a_reason_pass(self, node, tmp_path):
        webui = demo(
            tmp_path,
            {
                "routes/a/+page.svelte": (
                    '<script lang="ts">\nimport { Alert, Button, TechDetails } from "$lib/components/ui";\n</script>\n'
                    "<Button loading>Save</Button>\n"
                    '<TechDetails text="errno 5" />\n'
                    "<!-- design-ignore: where the restored items went, to read before going there -->\n"
                    '<Alert variant="success">Restored to /a</Alert>\n'
                    "<!-- design-ignore: the export's options, which its spec opens -->\n"
                    "<details><summary>Options</summary></details>\n"
                ),
                "lib/components/ui/Button.svelte": '<span class="loading-spinner animate-spin"></span>\n',
                "lib/components/ui/TechDetails.svelte": "<details><summary>Details</summary></details>\n",
                "lib/components/ui/Thing.svelte": '<Alert variant="success">x</Alert>\n',
            },
        )
        result = run_check(node, webui)
        assert result.returncode == 0, result.stderr
        assert "2 use(s) exempted" in result.stdout

    def test_a_comment_without_a_reason_keeps_nothing(self, node, tmp_path):
        result = check(node, tmp_path, '<!-- design-ignore: -->\n<Alert variant="success">x</Alert>\n<!-- design-ignore -->\n<details></details>\n')
        assert result.returncode == 1
        assert "a success Alert" in result.stderr and "raw <details>" in result.stderr


def test_make_frontend_runs_the_check():
    package = json.loads((WEBUI / "package.json").read_text(encoding="utf-8"))
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")

    assert package["scripts"]["design"] == "node scripts/design-check.mjs"
    assert "frontend-design frontend-build" in makefile
    assert "frontend-design:\n\tcd webui && $(NPM_BIN) run design\n" in makefile


def test_the_real_web_ui_passes(node):
    result = run_check(node)
    assert result.returncode == 0, result.stderr
