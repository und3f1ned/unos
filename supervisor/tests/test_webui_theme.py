"""The colour tokens of the web UI in its two themes (webui.light-theme, ADR-0405).

Every colour token of webui/tailwind.config.js is a CSS variable of webui/src/app.css with a value
for the dark theme and one for the light theme, so that a page that uses the tokens follows the
theme. The text and the controls meet WCAG AA in both themes: the contrast of each pair of tokens
that text uses is at least 4.5:1, and that of the boundaries of controls, the focus ring and the
graphics that carry meaning (status dots, chart lines) at least 3:1 (WCAG 1.4.3 and 1.4.11).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

WEBUI = Path(__file__).resolve().parents[2] / "webui"
APP_CSS = WEBUI / "src" / "app.css"
CONFIG = WEBUI / "tailwind.config.js"
THEMES = ("dark", "light")

TEXT = 4.5  # WCAG 1.4.3, normal text
NON_TEXT = 3.0  # WCAG 1.4.11, boundaries of controls and graphics (and 1.4.3's large text)

# A background is a stack of layers, the lowest first: a token with its opacity.
SURFACE = (("surface", 1.0),)
ELEVATED = (("surface-elevated", 1.0),)
OVERLAY = (("surface-overlay", 1.0),)
HOVER = (("surface-hover", 1.0),)
# .card-nested: surface-muted (half transparent, tailwind.config.js) on a card or a dialog.
NESTED = (("surface-elevated", 1.0), ("surface-muted", 0.5))


def over(base: tuple, token: str, opacity: float) -> tuple:
    return base + ((token, opacity),)


STATUSES = ("status-success", "status-warning", "status-error", "status-info")
ACCENTS = ("accent-cpu", "accent-memory", "accent-network", "accent-storage", "accent-container",
           "accent-temperature")
SOLID_FILLS = ("brand", "brand-hover", "solid-success", "solid-success-hover", "solid-warning",
               "solid-warning-hover", "solid-error", "solid-error-hover")


# The backgrounds that text and badges sit on: the page, a card or dialog, a nested card, and a
# row, field, secondary button or card under the pointer (surface-overlay).
BACKGROUNDS = (("surface", SURFACE), ("surface-elevated", ELEVATED), ("card-nested", NESTED),
               ("surface-overlay", OVERLAY))


def text_pairs() -> list[tuple[str, tuple, float, str]]:
    """(text token, background, minimum, where): the pairs that text uses."""
    pairs = []
    for name, background in BACKGROUNDS:
        for text in ("content", "content-soft", "content-secondary", "content-muted"):
            pairs.append((text, background, TEXT, f"text on {name}"))
        for status in STATUSES:
            pairs.append((status, background, TEXT, f"status text on {name}"))
            # Badge: the colour on its 20 % tint; Alert: on its 10 % tint.
            pairs.append((status, over(background, status, 0.2), TEXT, f"Badge on {name}"))
            pairs.append((status, over(background, status, 0.1), TEXT, f"Alert on {name}"))
        pairs.append(("content-secondary", over(background, "status-neutral", 0.2), TEXT, f"Badge neutral on {name}"))
        for accent in ACCENTS:
            pairs.append((accent, background, TEXT, f"accent text on {name}"))
        for accent in ("accent-memory", "accent-container", "accent-temperature"):
            pairs.append((accent, over(background, accent, 0.2), TEXT, f"Badge purple, cyan, orange on {name}"))
        # A selected row of the file manager; a search match of System > Logs (a 20 % tint).
        pairs.append(("content", over(background, "brand", 0.2), TEXT, f"a selected row on {name}"))
    pairs.append(("content", HOVER, TEXT, "a secondary button under the pointer"))
    for fill in SOLID_FILLS:
        pairs.append(("content-on-brand", ((fill, 1.0),), TEXT,
                      "buttons, the active tab and navigation item, a count on the selected category"))
    return pairs


def non_text_pairs() -> list[tuple[str, tuple, float, str]]:
    """(token, background, minimum, where): boundaries of controls and graphics that carry meaning."""
    pairs = []
    for name, background in BACKGROUNDS:
        pairs.append(("border-strong", background, NON_TEXT, f"the boundary of a field on {name}"))
        pairs.append(("brand-ring", background, NON_TEXT,
                      f"the focus ring of every button, the selected tab's line, a checked radio or checkbox on {name}"))
        for token in (*STATUSES, "status-neutral", *ACCENTS):
            pairs.append((token, background, NON_TEXT, f"status dots, progress bars, icons, chart lines on {name}"))
    return pairs


# --- Reading the tokens ---------------------------------------------------------------------------

THEME_BLOCK = re.compile(r"\[data-theme='(dark|light)'\]\s*\{(?P<body>[^}]*)\}")
VARIABLE = re.compile(r"--color-([a-z-]+):\s*(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})\s*;")


def theme_colours() -> dict[str, dict[str, tuple[int, int, int]]]:
    css = APP_CSS.read_text()
    themes: dict[str, dict[str, tuple[int, int, int]]] = {}
    for match in THEME_BLOCK.finditer(css):
        name = match.group(1)
        assert name not in themes, f"app.css defines the {name} theme twice"
        themes[name] = {m.group(1): (int(m.group(2)), int(m.group(3)), int(m.group(4)))
                        for m in VARIABLE.finditer(match.group("body"))}
    return themes


def config_tokens() -> set[str]:
    """The tokens that tailwind.config.js reads from app.css: token('surface-elevated')."""
    return set(re.findall(r"token\('([a-z-]+)'(?:,\s*[0-9.]+)?\)", CONFIG.read_text()))


def luminance(rgb: tuple[float, float, float]) -> float:
    def channel(value: float) -> float:
        value /= 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def composite(colours: dict, layers: tuple) -> tuple[float, float, float]:
    result = (0.0, 0.0, 0.0)
    for token, opacity in layers:
        top = colours[token]
        result = tuple(t * opacity + r * (1 - opacity) for t, r in zip(top, result))
    return result


def contrast(a: tuple, b: tuple) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def describe(layers: tuple) -> str:
    return " over ".join(f"{token}" + (f"/{round(opacity * 100)}" if opacity < 1 else "")
                         for token, opacity in reversed(layers))


# --- The tests --------------------------------------------------------------------------------

def test_both_themes_give_every_token_a_value():
    themes = theme_colours()

    assert set(themes) == set(THEMES)
    assert themes["dark"].keys() == themes["light"].keys()
    assert config_tokens(), "tailwind.config.js reads no token of app.css"
    assert config_tokens() <= themes["dark"].keys(), sorted(config_tokens() - themes["dark"].keys())


def test_the_config_has_no_colour_of_its_own():
    """A colour written into tailwind.config.js would be the same in both themes."""
    colours = CONFIG.read_text().split("colors:", 1)[1].split("spacing:", 1)[0]

    assert re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(\s*\d|hsla?\(", colours) == []


@pytest.mark.parametrize("theme", THEMES)
def test_text_meets_wcag_aa(theme):
    colours = theme_colours()[theme]
    failures = []
    for token, layers, minimum, where in text_pairs():
        ratio = contrast(colours[token], composite(colours, layers))
        if ratio < minimum:
            failures.append(f"{token} on {describe(layers)}: {ratio:.2f}:1, below {minimum}:1 ({where})")

    assert failures == []


@pytest.mark.parametrize("theme", THEMES)
def test_controls_and_graphics_meet_wcag_aa(theme):
    colours = theme_colours()[theme]
    failures = []
    for token, layers, minimum, where in non_text_pairs():
        ratio = contrast(colours[token], composite(colours, layers))
        if ratio < minimum:
            failures.append(f"{token} against {describe(layers)}: {ratio:.2f}:1, below {minimum}:1 ({where})")

    assert failures == []


def test_the_contrast_is_computed_as_wcag_defines_it():
    white, black, blue = (255, 255, 255), (0, 0, 0), (37, 99, 235)

    assert contrast(white, black) == pytest.approx(21.0)
    assert contrast(white, white) == pytest.approx(1.0)
    assert contrast(white, blue) == pytest.approx(5.17, abs=0.01)
    # Half of black over white is the grey between them.
    assert composite({"w": white, "b": black}, (("w", 1.0), ("b", 0.5))) == (127.5, 127.5, 127.5)


PALETTE = ("slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|"
           "indigo|violet|purple|fuchsia|pink|rose")
RAW_COLOUR = re.compile(
    rf"(?<![\w-])(?:[a-z0-9-]+:)*!?(?:bg|text|border|ring|ring-offset|divide|outline|from|via|to|fill|stroke|"
    rf"accent|caret|shadow|decoration|placeholder)-(?:(?:{PALETTE})-\d{{2,3}}|white|black)(?:/\d+)?(?![\w-])"
    rf"|theme\('colors\.(?:{PALETTE}|white|black)")


@pytest.mark.parametrize("path", [
    APP_CSS, WEBUI / "src" / "app.html", *sorted((WEBUI / "src" / "lib" / "components" / "ui").glob("*.svelte")),
], ids=lambda path: path.name)
def test_the_design_systems_own_files_use_the_tokens(path):
    """scripts/design-check.mjs leaves out app.css, app.html and the components of ui (ADR-0400):
    a palette colour there would stay the same in both themes."""
    assert RAW_COLOUR.findall(path.read_text()) == []


COLOR_SCHEME = re.compile(r"(?<!prefers-)color-scheme\s*:")


def test_only_the_themes_set_the_browsers_colour_scheme():
    """app.css sets color-scheme with each theme: one fixed elsewhere would draw the browser's own
    parts of a control (a date field's calendar button, a scrollbar) for the other theme."""
    files = [path for path in (WEBUI / "src").rglob("*") if path.suffix in (".svelte", ".ts", ".css", ".html")]

    assert [path.relative_to(WEBUI).as_posix() for path in files
            if path != APP_CSS and COLOR_SCHEME.search(path.read_text())] == []
    assert len(COLOR_SCHEME.findall(APP_CSS.read_text())) == 2


def test_the_page_sets_the_theme_before_it_is_drawn():
    """app.html loads static/theme-init.js, a file and not an inline script (the CSP allows only
    SvelteKit's own inline script), in the head; it reads the cache that the theme store keeps."""
    html = (WEBUI / "src" / "app.html").read_text()
    head = html.split("</head>", 1)[0]
    init = (WEBUI / "static" / "theme-init.js").read_text()
    store = (WEBUI / "src" / "lib" / "stores" / "theme.ts").read_text()
    key = re.search(r"THEME_KEY = '([^']+)'", store)

    assert '<script src="/theme-init.js"></script>' in head
    assert head.index("theme-init.js") < head.index("%sveltekit.head%")
    assert key and f"'{key.group(1)}'" in init
    assert "data-theme" in init and "prefers-color-scheme: light" in init
    # Before the style sheet comes, the browser paints its canvas for this scheme.
    assert "style.colorScheme = theme" in init
