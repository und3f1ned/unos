---
status: accepted
date: 2026-11-18
area: webui
decided-by: agent
---

# ADR-0400: A check fails for raw controls and raw colours in the web UI's pages

## Context

ADR-0012 gives the web UI a design system, the components of `webui/src/lib/components/ui` and
the colour tokens of `webui/tailwind.config.js`, but older pages kept about 90 raw `<button>`
elements and 500 raw palette classes (`bg-slate-700`, `text-red-400`), so that a change of a
colour or of a button's look meant editing every page, and the planned light theme
(webui.light-theme) would have found the colours spread over the markup. The owner delegated the
decisions of this entry to the agent, asking above all that they be safe.

## Decision

- `webui/scripts/design-check.mjs` (`npm run design`, `make frontend-design`, part of
  `make frontend` and so of `make check`) reads every `.svelte` file of `webui/src` outside
  `src/lib/components/ui` with Svelte's and TypeScript's parsers, and fails for:
  - a raw `<button>`: use `Button`; its variant `plain` brings no look of its own, for a control
    that styles itself (a tab, a clickable card or row), and `Button` passes other attributes
    (`title`, `aria-*`, `role`, `id`, `tabindex`) and `keydown` on, and `bind:el` gives the
    element;
  - a raw `<a>` styled as a button (a `btn` class): use `Button` with `href`;
  - a raw `<input>`, `<select>` or `<textarea>` without a `form-*` class of `src/app.css`. Decided
    for the fields: `Input` and `Select` stay the choice for labelled fields, but a field that
    needs what they lack (`required`, `minlength`, `pattern`, a type that is not static, a password
    with a toggle) may stay a raw element with `form-input` or `form-select`, which carry the
    tokens. Checkboxes, radios, file, hidden and range inputs are exempt: the browser draws them;
  - a raw palette colour: a utility with a Tailwind palette colour and shade, or `white` or
    `black` (`bg-slate-700`, `hover:text-red-400`, `border-green-500/50`, `ring-white`), in a
    `class` attribute, a `class:` directive or any string literal of the markup or of a script.
    The message names the token to use.
- `// design-ignore: <reason>` above or at the end of a script statement or property, and
  `<!-- design-ignore: <reason> -->` above an element, exempt what stands under it (an element, or the nearest statement or property and what it holds),
  the reason being required; review reads them and the summary counts the colours and controls
  exempted. No page uses one.
- The check reads `tailwind.config.js` and fails when its table of replacements names a token the
  config lacks. `supervisor/tests/test_webui_design_system.py` runs it on good and bad samples and
  on the real web UI.
- The mapping of raw colours to tokens (the roles of ADR-0012, plus tokens added to keep the look):

  | Raw colour | Token |
  | --- | --- |
  | `slate-100` and lighter text | `content` |
  | `slate-300` text (new) | `content-soft` |
  | `slate-400` text | `content-secondary` |
  | `slate-500` text | `content-muted` |
  | `slate-500` fill (off, unknown; new) | `status-neutral` |
  | `slate-600` fill (hover over `surface-overlay`; new) | `surface-hover` |
  | `slate-700` fill, `slate-700/50` fill | `surface-overlay`, `surface-muted` |
  | `slate-800`, `slate-900` fill | `surface-elevated`, `surface` |
  | `slate-600` border (fields; new) | `border-strong` |
  | `slate-700` border, `slate-700/50` border | `border`, `border-muted` |
  | `red-*`, `green-*`, `yellow-*`, `blue-*` up to 500 | `status-error`, `status-success`, `status-warning`, `status-info` |
  | `blue-500` line or ring, `blue-600`, `blue-700`, `blue-800` (new) | `brand-ring`, `brand`, `brand-hover`, `brand-disabled` |
  | `orange-*`, `purple-*`, `cyan-*` | `accent-temperature`, `accent-memory`, `accent-container` |
  | `white` text on a solid fill (new) | `content-on-brand` |
  | `black/50` backdrop | the class `scrim` of `src/app.css` |
  | `red-600`, `green-600`, `orange-600` solid fills | the variants of `Button`, `Badge` and `Alert` |

  A tint such as `bg-red-500/20` becomes `bg-status-error/20` (the 400 shade of the token: the
  difference is not visible). Tabs are the classes `tab-pill` and `tab-underline`, with
  `tab-pill-active` and `tab-underline-active`, on a `Button` of variant `plain`.
- The new tokens have the hex values of the Tailwind colours they replace, so that pages look the
  same but for the steps listed in the Consequences; this ADR adds no theme (webui.light-theme builds on the tokens).

## Consequences

- A new button or colour class that bypasses the design system fails `make check`, with the file,
  the line and the replacement.
- Buttons without a `type` inside a form were submit buttons; `Button` defaults to `type="button"`,
  so that a Cancel button there no longer submits.
- Some looks moved by one step: a warning button is orange, as `Button`'s variant is; icon buttons
  show `surface-overlay` on hover where they showed `slate-600`; fields take the padding of
  `form-input`.
- The check cannot see a colour put together at run time (`'bg-' + name`), an arbitrary value
  (`bg-[#fff]`, `stroke="#3b82f6"`), a variant such as `[&_svg]:` or the classes a `.ts` module
  returns (`$lib/api/logs.ts` and `health.ts` were moved to tokens by hand): review does.
- A coloured icon button is a `ghost` `Button` with its own `text-status-*` and the same
  `hover:text-status-*`, since `.btn-ghost` turns the text to `content` on hover; a `hover:blue-500`
  or a `300` shade became the token's shade.
- `src/app.css`, the components of `ui` and `tailwind.config.js` still use the palette; the
  light theme moves them to tokens.
