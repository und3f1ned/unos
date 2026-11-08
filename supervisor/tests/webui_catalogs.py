"""The web UI's texts for the tests that tie them to the server's values (ADR-0287).

* ``read_catalog(area)``: the en and ru halves of webui/src/lib/i18n/catalog/<area>.ts, read with
  node and TypeScript as webui/scripts/i18n-check.mjs reads them. Without node or without
  webui/node_modules (make setup), the test skips.
* ``key_map(svelte, name)``: a map of a .svelte file from a value that the server answers to the
  key of its text in a catalog, ``const NAME: Record<...> = { value: "area.key", ... };``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WEBUI = REPO / "webui"
CATALOGS = WEBUI / "src" / "lib" / "i18n" / "catalog"

# Prints {en, ru} of the catalog named by its last argument.
READ_CATALOG = r"""
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const [packageJson, catalogTs] = process.argv.slice(-2);
const ts = createRequire(packageJson)('typescript');
const { outputText } = ts.transpileModule(readFileSync(catalogTs, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
});
const { en, ru } = await import('data:text/javascript;base64,' + Buffer.from(outputText).toString('base64'));
process.stdout.write(JSON.stringify({ en, ru }));
"""

# One entry of a map: a value (a name, or a quoted text such as "5 minutes ago" or "") and a key.
ENTRY = re.compile(r"""(?:"([^"]*)"|'([^']*)'|([A-Za-z_$][\w$]*))\s*:\s*["']([a-z][a-zA-Z0-9]*\.[\w.]+)["']""")


def read_catalog(area: str) -> dict[str, dict]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed (Node.js 22, webui/package.json)")
    if not (WEBUI / "node_modules" / "typescript" / "package.json").exists():
        pytest.skip("webui/node_modules has no typescript: make setup installs it")
    result = subprocess.run(
        [node, "--input-type=module", "-e", READ_CATALOG, str(WEBUI / "package.json"), str(CATALOGS / f"{area}.ts")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def key_map(svelte: Path, name: str) -> dict[str, str]:
    """The keys of the catalog by value, in the order of the map."""
    source = svelte.read_text(encoding="utf-8")
    match = re.search(rf"\bconst {name}\b[^=]*=\s*\{{(?P<body>.*?)\n\s*\}};", source, re.S)
    assert match, f"{svelte.name} has no map {name}"
    return {double or single or bare: key for double, single, bare, key in ENTRY.findall(match.group("body"))}
