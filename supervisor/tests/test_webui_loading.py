"""The web UI loads without flicker (webui.loading-without-flicker, ADR-0430).

The boot screen of webui/src/app.html is drawn by the HTML and app.css alone, from the first frame,
hidden from screen readers, and fades out in under 200 ms once $lib/boot.ts marks it done; the
Skeleton component carries no text and no role; no text of the catalogs says "Loading". The
behaviour in the browser (the frames, the layout shift, the shell drawn once) is checked by
webui/tests/loading.spec.ts on the stand-in.
"""
from __future__ import annotations

import re
from pathlib import Path

WEBUI = Path(__file__).resolve().parents[2] / "webui"
SRC = WEBUI / "src"
APP_HTML = SRC / "app.html"
APP_CSS = SRC / "app.css"
BOOT_TS = SRC / "lib" / "boot.ts"
SKELETON = SRC / "lib" / "components" / "ui" / "Skeleton.svelte"


def css_rule(selector: str) -> str:
    """The body of the first rule of app.css whose selector is exactly `selector`."""
    match = re.search(rf"(?m)^\s*{re.escape(selector)}\s*\{{(?P<body>[^}}]*)\}}", APP_CSS.read_text())
    assert match, f"app.css has no rule {selector}"
    return match.group("body")


def test_the_boot_screen_is_in_the_html_before_the_app():
    html = APP_HTML.read_text()
    body = html.split("<body", 1)[1]
    boot = re.search(r'<div id="boot" class="boot" aria-hidden="true">', body)
    assert boot, "app.html draws the boot screen, hidden from screen readers"
    assert body.index('id="boot"') < body.index("%sveltekit.body%")
    # No script of its own: the CSP allows only SvelteKit's inline script and files.
    assert re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html) == []
    # No text and no role that a screen reader or a spec could take for the page's.
    boot_markup = body[boot.start(): body.index("%sveltekit.body%")]
    assert "role=" not in boot_markup
    assert re.sub(r"<[^>]+>|<!--.*?-->", "", boot_markup, flags=re.S).strip() == ""


def test_the_boot_screen_fades_out_in_under_200_ms_and_lets_the_pointer_through():
    rule = css_rule(".boot")
    fades = re.findall(r"opacity\s+(\d+)ms", rule)
    assert fades and all(int(ms) < 200 for ms in fades)
    assert "background-color: rgb(var(--color-surface))" in rule
    done = css_rule(".boot[data-done]")
    assert "opacity: 0" in done and "pointer-events: none" in done and "visibility: hidden" in done
    fade_ms = int(re.search(r"const FADE_MS = (\d+);", BOOT_TS.read_text()).group(1))
    assert int(fades[0]) <= fade_ms < 200 + 50


def test_under_reduced_motion_the_skeletons_and_the_boot_bar_do_not_move():
    css = APP_CSS.read_text()
    reduced = css[css.index("@media (prefers-reduced-motion: reduce)"):]
    reduced = reduced[: reduced.index("\n}\n")]
    assert re.search(r"\.skeleton\s*\{\s*animation: none !important;", reduced)
    assert ".boot-progress::after" in reduced and "boot-breathe" in reduced


def test_a_skeleton_is_hidden_from_screen_readers_and_has_no_text_or_role():
    markup = SKELETON.read_text().split("</script>", 1)[1]
    roots = re.findall(r"<(div|span|tr)\b[^>]*\bdata-skeleton\b[^>]*>", markup)
    assert len(roots) >= 7, "every shape marks its root"
    for root in re.findall(r"<(?:div|span|tr)\b[^>]*\bdata-skeleton\b[^>]*>", markup):
        assert 'aria-hidden="true"' in root
    assert "$t(" not in markup and "role=" not in markup


def test_no_text_of_the_web_ui_says_loading():
    catalogs = sorted((SRC / "lib" / "i18n" / "catalog").glob("*.ts"))
    texts = [(path.name, text) for path in catalogs
             for text in re.findall(r"""'[a-zA-Z0-9.]+': (?:'([^'\n]*)'|"([^"\n]*)")""", path.read_text())]
    texts = [(name, single or double) for name, (single, double) in texts]
    assert [(name, text) for name, text in texts if re.search(r"\bLoading\b", text)] == []
    assert [path.relative_to(WEBUI).as_posix() for path in SRC.rglob("*")
            if path.suffix in (".svelte", ".css") and "loading-text" in path.read_text()] == []
