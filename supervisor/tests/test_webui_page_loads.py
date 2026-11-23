"""The web UI's pages read their data in SvelteKit load functions (webui.navigation-without-skeletons, ADR-0469).

Every +page.ts of webui/src/routes loads through readPage of $lib/pages/load, which reads for the
signed-in account only, keeps the page's snapshot in the tab's cache and says whom it read for.
A link preloads its page's data on tap, which app.html's body sets for every link; the sidebar's
links take preloadOf of $lib/pages/preload, which answers hover only for the pages of HOVER_PAGES.
Such a page's reader may ask only the API modules that read no data disk and answer at once
(HOVER_SAFE_API): a pointer passing over a link must never wake a sleeping disk (ADR-0378). What a
navigation shows in the browser is checked by webui/tests/page-loads.spec.ts on the stand-in.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

WEBUI = Path(__file__).resolve().parents[2] / "webui"
SRC = WEBUI / "src"

# The API modules of webui/src/lib/api whose reads touch no data disk and answer within a moment:
# the database and the account. A module joins only after its server code was checked for both
# (ADR-0469); storage, health, shares, files, snapshots, iscsi and backup read the pools' disks, and
# containers and apps wait for Docker.
HOVER_SAFE_API = {"auth", "groups", "audit", "notifications", "dashboard", "theme"}

# The shared modules of $lib/pages, which ask no area of the API themselves. A reader that imports
# $lib/api/client, the transport, could ask any path: it counts as the module `client`, not safe.
SHARED = {"load", "cache", "read", "follow.svelte", "preload"}

# An import of values (an `import type` reads nothing).
IMPORT = re.compile(r"""^\s*import\s(?!type\s)[^;]*?from\s+['"]([^'"]+)['"]""", re.M)


def imports(file: Path) -> list[str]:
    return IMPORT.findall(file.read_text())


def hover_pages(preload: Path) -> list[str]:
    match = re.search(r"export const HOVER_PAGES: readonly string\[\] = \[(?P<list>[^\]]*)\];", preload.read_text())
    assert match, f"{preload} declares HOVER_PAGES as a list of paths"
    return re.findall(r"'([^']+)'", match.group("list"))


def route_file(src: Path, page: str) -> Path:
    return src / "routes" / page.strip("/") / "+page.ts"


def api_modules(src: Path, file: Path, seen: set[Path] | None = None) -> set[str]:
    """The modules of $lib/api that `file` imports, also through the modules of $lib/pages it imports."""
    seen = seen if seen is not None else set()
    if file in seen:
        return set()
    seen.add(file)
    found: set[str] = set()
    for name in imports(file):
        if name.startswith("$lib/api/"):
            found.add(name.removeprefix("$lib/api/"))
        elif name.startswith("$lib/pages/") or (name.startswith("./") and file.parent == src / "lib" / "pages"):
            module = name.removeprefix("$lib/pages/").removeprefix("./")
            target = src / "lib" / "pages" / f"{module}.ts"
            if module not in SHARED and target.is_file():
                found |= api_modules(src, target, seen)
    return found


def hover_problems(src: Path) -> list[str]:
    """Each page of HOVER_PAGES whose load asks an API module outside HOVER_SAFE_API, or has no load."""
    problems = []
    for page in hover_pages(src / "lib" / "pages" / "preload.ts"):
        route = route_file(src, page)
        if not route.is_file():
            problems.append(f"{page} is in HOVER_PAGES but has no {route.relative_to(src)}")
            continue
        unsafe = sorted(api_modules(src, route) - HOVER_SAFE_API)
        if unsafe:
            problems.append(f"{page} preloads on hover, but its load asks $lib/api/{', $lib/api/'.join(unsafe)}")
    return problems


def test_a_link_preloads_its_pages_data_on_tap_unless_it_says_otherwise():
    body = (SRC / "app.html").read_text().split("<body", 1)[1].split(">", 1)[0]
    assert 'data-sveltekit-preload-data="tap"' in body
    nav = (SRC / "lib" / "components" / "layout" / "NavMenu.svelte").read_text()
    assert "data-sveltekit-preload-data={preloadOf(item.href)}" in nav


def test_only_pages_whose_loads_read_no_data_disk_preload_on_hover():
    assert hover_problems(SRC) == []


@pytest.mark.parametrize(
    ("page", "unsafe"),
    [("/", {"storage", "containers", "health"}), ("/storage", {"storage"}), ("/shares", {"shares"})],
)
def test_the_pages_that_read_the_disks_could_not_preload_on_hover(page: str, unsafe: set[str]):
    # The dashboard reads the pools (btrfs scans every disk) and waits for Docker; Storage reads the
    # disks; Shares the volumes of the mounted pools, smbd's status and the network's interfaces.
    asked = api_modules(SRC, route_file(SRC, page))
    assert unsafe <= asked and not asked <= HOVER_SAFE_API


def test_every_page_load_reads_through_read_page():
    loads = sorted(SRC.glob("routes/**/+page.ts"))
    assert loads, "the pages have loads"
    for load in loads:
        text = load.read_text()
        assert "from '$lib/pages/load'" in text and "readPage(event, " in text, load.relative_to(SRC)
    # No page reads on the server: the static adapter, and no +page.server.ts or +layout.server.ts.
    assert not list(SRC.glob("routes/**/+*.server.ts"))


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.mark.parametrize(
    ("reader", "expected"),
    [
        ("import { getUsers } from '$lib/api/auth';\n", []),
        ("import { getPools } from '$lib/api/storage';\n", ["/users preloads on hover, but its load asks $lib/api/storage"]),
        ("import { other } from './other';\n", ["/users preloads on hover, but its load asks $lib/api/health"]),
        ("import { api } from '$lib/api/client';\n", ["/users preloads on hover, but its load asks $lib/api/client"]),
        ("import type { Pool } from '$lib/api/storage';\n", []),
    ],
    ids=["database-only", "reads-the-disks", "through-another-reader", "any-path", "a-type-only"],
)
def test_the_check_follows_a_hover_pages_reads(tmp_path: Path, reader: str, expected: list[str]):
    src = tmp_path / "src"
    write(src / "lib" / "pages" / "preload.ts", "export const HOVER_PAGES: readonly string[] = ['/users'];\n")
    write(src / "lib" / "pages" / "users.ts", reader)
    write(src / "lib" / "pages" / "other.ts", "import { getDisks } from '$lib/api/health';\n")
    write(
        src / "routes" / "users" / "+page.ts",
        "import { readPage } from '$lib/pages/load';\nimport { usersReader } from '$lib/pages/users';\n",
    )
    assert hover_problems(src) == expected


def test_the_check_refuses_a_hover_page_without_a_load(tmp_path: Path):
    src = tmp_path / "src"
    write(src / "lib" / "pages" / "preload.ts", "export const HOVER_PAGES: readonly string[] = ['/audit'];\n")
    assert hover_problems(src) == ["/audit is in HOVER_PAGES but has no routes/audit/+page.ts"]
