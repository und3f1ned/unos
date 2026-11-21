"""ESLint and Prettier check the web UI's code in make frontend and CI (ADR-0449).

webui/eslint.config.js holds the recommended rules of ESLint, typescript-eslint and
eslint-plugin-svelte, with the rules it turns off and their reasons, and webui/prettier.config.js
the format: tabs, single quotes, no trailing commas and lines of up to 120 columns. `make frontend`
runs `npm run lint` (ESLint over src/) and `npm run format-check` (Prettier's check of src/), also
when another part fails (ADR-0190), and the CI workflow's Frontend job runs `make frontend`. Each
test lints or checks a small web UI in tmp_path with the web UI's own configuration and tools: a
finding of each set of rules fails ESLint, and a file that Prettier would change fails its check.
`make frontend` runs both over the real web UI.

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
ESLINT = WEBUI / "node_modules" / "eslint" / "bin" / "eslint.js"
PRETTIER = WEBUI / "node_modules" / "prettier" / "bin" / "prettier.cjs"


@pytest.fixture(scope="module")
def node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed (Node.js 22, webui/package.json)")
    if not ESLINT.exists() or not PRETTIER.exists():
        pytest.skip("webui/node_modules has no eslint or prettier: make setup installs them")
    return node


def write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))


def test_make_frontend_runs_eslint_and_prettiers_check_over_src():
    scripts = json.loads((WEBUI / "package.json").read_text())["scripts"]
    makefile = (REPO / "Makefile").read_text()

    assert scripts["lint"] == "eslint --max-warnings 0 src"
    assert scripts["format-check"] == "prettier --check src"
    assert scripts["format"] == "prettier --write src"
    assert "frontend-build frontend-lint frontend-format)" in makefile
    assert "frontend-lint:\n\tcd webui && $(NPM_BIN) run lint\n" in makefile
    assert "frontend-format:\n\tcd webui && $(NPM_BIN) run format-check\n" in makefile


def lint(node: str, root: Path) -> tuple[subprocess.CompletedProcess, set[tuple[str, str]]]:
    # The web UI's configuration, read from a folder of its own: its `files` match below it.
    (root / "eslint.config.js").write_text(f"export {{ default }} from {json.dumps((WEBUI / 'eslint.config.js').as_uri())};\n")
    (root / "tsconfig.json").write_text(json.dumps({
        "compilerOptions": {"strict": True, "module": "esnext", "moduleResolution": "bundler", "target": "esnext"},
        "include": ["src/**/*.ts", "src/**/*.svelte"],
    }))
    # The options of `npm run lint`, with a report that the test reads.
    options = json.loads((WEBUI / "package.json").read_text())["scripts"]["lint"].split()[1:]
    result = subprocess.run([node, str(ESLINT), "--format", "json", *options], cwd=root, capture_output=True, text=True,
                            timeout=300)
    found = {
        (Path(file["filePath"]).relative_to(root.resolve()).as_posix(), message["ruleId"])
        for file in json.loads(result.stdout or "[]")
        for message in file["messages"]
    }
    return result, found


def test_eslint_fails_for_a_finding_of_each_set_of_rules(node, tmp_path):
    write(tmp_path, {
        # ESLint's own recommended rules, in a module.
        "src/lib/debug.ts": "export function stop(): void {\n\tdebugger;\n}\n",
        # typescript-eslint's, in a module, a component's script and a module with runes.
        "src/lib/any.ts": "export function echo(value: any) {\n\treturn value;\n}\n",
        "src/lib/count.svelte.ts": "export const count = $state<any>(0);\n",
        # eslint-plugin-svelte's, in the markup of a component in TypeScript.
        "src/lib/Demo.svelte": """\
            <script lang="ts">
            \tlet { html }: { html: string } = $props();
            \tconst shown: any = html;
            </script>

            {@html shown}
            """,
        # What the configuration turns off, with its reasons: an {#each} without a key, a space
        # written as a string, a link without resolve() and a Set of a component.
        "src/lib/List.svelte": """\
            <script lang="ts">
            \tlet { items }: { items: string[] } = $props();
            \tconst seen = new Set(items);
            </script>

            <p>{#each items as item}{' '}<span>{item}</span>{/each}</p>
            <a href="/storage">{seen.size}</a>
            """,
        "src/lib/clean.ts": "export const answer = 42;\n",
    })

    result, found = lint(node, tmp_path)

    assert result.returncode == 1, result.stderr
    assert found == {
        ("src/lib/debug.ts", "no-debugger"),
        ("src/lib/any.ts", "@typescript-eslint/no-explicit-any"),
        ("src/lib/count.svelte.ts", "@typescript-eslint/no-explicit-any"),
        ("src/lib/Demo.svelte", "@typescript-eslint/no-explicit-any"),
        ("src/lib/Demo.svelte", "svelte/no-at-html-tags"),
    }, result.stdout


def test_a_warning_or_an_exemption_that_exempts_nothing_fails_eslint(node, tmp_path):
    # $inspect is a warning of eslint-plugin-svelte's recommended rules: it fails the run too.
    write(tmp_path, {
        "src/lib/Inspect.svelte": "<script lang=\"ts\">\n\tlet n = $state(0);\n\t$inspect(n);\n</script>\n\n<p>{n}</p>\n",
        "src/lib/exempt.ts": "// eslint-disable-next-line no-debugger -- nothing to exempt\nexport const answer = 42;\n",
    })

    result, found = lint(node, tmp_path)

    assert result.returncode == 1, result.stdout + result.stderr
    assert found == {("src/lib/Inspect.svelte", "svelte/no-inspect"), ("src/lib/exempt.ts", None)}, result.stdout


def test_eslint_passes_a_web_ui_without_findings(node, tmp_path):
    write(tmp_path, {
        "src/lib/clean.ts": "export const answer = 42;\n",
        "src/lib/Clean.svelte": "<script lang=\"ts\">\n\tlet { n }: { n: number } = $props();\n</script>\n\n<p>{n}</p>\n",
    })

    result, found = lint(node, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert found == set()


def prettier(node: str, root: Path, mode: str) -> subprocess.CompletedProcess:
    # From webui/, where Prettier finds the plugin that its configuration names.
    return subprocess.run([node, str(PRETTIER), "--config", str(WEBUI / "prettier.config.js"), mode, str(root / "src")],
                          cwd=WEBUI, capture_output=True, text=True, timeout=300)


# A call of 120 columns, with the indentation of one tab, which Prettier counts as two (tabWidth),
# and one of 121.
def call(last: str) -> str:
    return "\tconsole.log(" + ", ".join(["'abcdefghijklmn'"] * 5 + [f"'{last}'"]) + ");"


def test_prettier_fails_for_a_file_it_would_change_and_formats_with_the_web_uis_settings(node, tmp_path):
    kept, broken = call("abcdefghijkl"), call("abcdefghijklm")
    assert (len(kept.expandtabs(2)), len(broken.expandtabs(2))) == (120, 121)
    write(tmp_path, {
        # Double quotes, a trailing comma, spaces for indentation and a line of 121 columns.
        "src/lib/style.ts": 'export const pair = {\n  a: "one",\n  b: \'two\',\n};\n'
                            f"export function log(): void {{\n{broken.expandtabs(2)}\n}}\n",
        "src/lib/Demo.svelte": '<script lang="ts">\n  let { n }: { n: number } = $props();\n</script>\n\n<p>{n}</p>\n',
        # As Prettier writes it: tabs, single quotes, no trailing commas, a line of 120 columns kept.
        "src/lib/kept.ts": f"export const pair = {{ a: 'one', b: 'two' }};\nexport function log(): void {{\n{kept}\n}}\n",
    })

    check = prettier(node, tmp_path, "--check")

    assert check.returncode == 1, check.stdout + check.stderr
    assert "src/lib/style.ts" in check.stderr + check.stdout
    assert "src/lib/Demo.svelte" in check.stderr + check.stdout
    assert "kept.ts" not in check.stderr + check.stdout

    written = prettier(node, tmp_path, "--write")

    assert written.returncode == 0, written.stderr
    assert prettier(node, tmp_path, "--check").returncode == 0
    assert (tmp_path / "src/lib/style.ts").read_text() == (
        "export const pair = {\n\ta: 'one',\n\tb: 'two'\n};\nexport function log(): void {\n\tconsole.log(\n"
        + "\t\t'abcdefghijklmn',\n" * 5
        + "\t\t'abcdefghijklm'\n\t);\n}\n"
    )
    assert (tmp_path / "src/lib/Demo.svelte").read_text() == (
        '<script lang="ts">\n\tlet { n }: { n: number } = $props();\n</script>\n\n<p>{n}</p>\n'
    )
