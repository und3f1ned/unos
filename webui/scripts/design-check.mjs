#!/usr/bin/env node
// Pages are built from the design system: the components of src/lib/components/ui and the colour
// tokens of the @theme of src/app.css (ADR-0012, ADR-0400). make frontend runs this check (npm run
// design); supervisor/tests/test_webui_design_system.py tests it.
//
//   node scripts/design-check.mjs [webui directory]      (default: the webui/ of this script)
//
// It reads every .svelte file under src/ except those of src/lib/components/ui, and fails for:
// - a raw <button>: use Button (variants and sizes, `plain` for a control that styles itself:
//   a tab, a clickable card or row);
// - a raw <a> that is styled as a button (a `btn` class): use Button with `href`;
// - a raw <input> (not a checkbox, a radio, a file or a hidden one), <select> or <textarea>
//   without a `form-*` class of src/app.css (form-input, form-select): use Input or Select, or
//   give the field the class when it needs an attribute the component lacks (pattern, minlength);
//   since webui.fields-in-safari the class of its kind, form-select for a <select> and form-input for the
//   others, and no class or style of its own that sets `appearance` (appearance-auto,
//   [appearance:menulist], style:appearance): Safari draws a select or a number field in the
//   browser's own look, of another height, unless the class of its kind draws it;
// - a raw Tailwind colour: a utility of the palette (bg-slate-700, text-red-400, hover:bg-blue-600,
//   border-green-500/50, ring-white) in a class attribute, a `class:` directive or any string
//   literal of the markup or a <script>. Use the token of the same role (the message names it);
//   a colour that no token covers is added to the @theme of src/app.css first;
// and, since ADR-0432:
// - the browser's own dialogs, window.confirm, window.alert and window.prompt (called so or bare,
//   in a .svelte file or a .ts module): use confirm() and toast of $lib/components/ui;
// - the class `uppercase`: a badge is sentence case, and a group label is the class `eyebrow`;
// - a raw checkbox or radio button (<input type="checkbox|radio">): use Checkbox, Switch or
//   RadioGroup;
// - a filled primary Button (the default variant) in a card's header (Card's `actions`, an element
//   of class card-header, a header row of a card: a justify-between box with an h3 to h6 or a
//   card-title in it) or in a table's row (tr, td, table-actions): use CardAction, a secondary or
//   ghost Button or an ActionMenu; the primary action is the page header's;
// and, since ADR-0438:
// - a success Alert (`variant="success"`, or a 'success' in its variant's expression): what an
//   action did is a toast; an outcome to read before going on keeps its Alert with a reason;
// - a hand-made spinner, the class `animate-spin` or app.css's `loading-spinner`: use Button's
//   `loading`, LoadState or Skeleton, and a StatusDot with `pulse` for a job that runs;
// - a raw <details>: use TechDetails;
// and, since ADR-0459, in every .svelte file and .ts module of src/ (ui's too) but the one home of the
// formats, src/lib/format.ts and src/lib/time.ts:
// - a number or a date written by hand: toFixed, toPrecision, toLocaleString, toLocaleDateString,
//   toLocaleTimeString, toDateString, toTimeString, toUTCString, Intl.NumberFormat, DateTimeFormat,
//   RelativeTimeFormat and DurationFormat, a date's getters (getFullYear to getSeconds, also of
//   UTC) and padStart(2, '0') (not of a number in another base, n.toString(16)), in a script or an
//   expression of the markup, and a percentage or a unit of a size put together after a value
//   (value + ' MB', `${value}%`, {value}% between tags or in an attribute other than style): use
//   the functions of the home;
// - in a catalog (src/lib/i18n/catalog/), a percentage put together in a text ('{value}%'), and a
//   Russian text with a digit right before % or a plain space between (Russian writes 80 %, with a
//   no-break space, as Intl does).
// A comment `design-ignore: <reason>` exempts what it stands for: above an element (an HTML comment
// <!-- design-ignore: ... -->), or above or at the end of the statement or property of a script
// (as `i18n-ignore:` does). Review reads the reasons; the summary counts the exemptions.

import { existsSync, readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse } from 'svelte/compiler';
import { walk } from './svelte-walk.mjs';
import ts from 'typescript';

const EXEMPTION = /design-ignore:\s*\S/;
const PALETTE = 'slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose';
const UTILITIES = 'bg|text|border|border-[xytblrse]|ring|ring-offset|divide|outline|from|via|to|fill|stroke|accent|caret|shadow|decoration|placeholder';
// One class: variants (hover:, md:, group-hover:), an optional `!`, the utility, the colour, an
// optional opacity (/50).
const COLOR = new RegExp(
	`^(?:[a-z0-9-]+:)*!?(${UTILITIES})-(?:(${PALETTE})-(\\d{2,3})|(white|black))(?:/(?:\\d+|\\[[^\\]]+\\]))?$`
);
const FIELD_TYPES_EXEMPT = new Set(['checkbox', 'radio', 'file', 'hidden', 'range', 'color', 'submit', 'button', 'reset', 'image']);
const UPPERCASE = /^(?:[a-z0-9-]+:)*!?uppercase$/;
// A utility that sets a field's `appearance` (appearance-auto, [-webkit-appearance:none]).
const APPEARANCE = /^(?:(?:[a-z0-9-]+|\[[^\]]+\]):)*!?(?:appearance-[a-z-]+|\[(?:-(?:webkit|moz)-)?appearance:[^\]]*\])!?$/;
const NATIVE_DIALOGS = new Set(['confirm', 'alert', 'prompt']);
const GLOBALS = new Set(['window', 'globalThis', 'self']);
const CHOICE_TYPES = new Set(['checkbox', 'radio']);
const SPINNER = /^(?:[a-z0-9-]+:)*!?(?:animate-spin|loading-spinner)$/;
const TAG_LIKE = new Set(['Element', 'InlineComponent', 'Slot', 'Head', 'Options', 'Window', 'Body', 'Document']);

const webui = path.resolve(process.argv[2] ?? path.join(path.dirname(fileURLToPath(import.meta.url)), '..'));
const SRC = path.join(webui, 'src');
const UI = path.join(SRC, 'lib', 'components', 'ui') + path.sep;
const relative = (file) => path.relative(webui, file).split(path.sep).join('/');
const errors = [];
let exempt = 0;

/** "line:column" of `offset` in `text`, both counted from 1. */
function position(text, offset) {
	const before = text.slice(0, offset).split('\n');
	return `${before.length}:${before[before.length - 1].length + 1}`;
}

// --- The tokens ----------------------------------------------------------------------------------

/**
 * The class names of the colour tokens ('surface-overlay', 'status-error'): the variables
 * --color-<name> of the @theme blocks of src/app.css (Tailwind CSS 4, ADR-0456).
 */
function tokenNames() {
	const css = path.join(webui, 'src', 'app.css');
	if (!existsSync(css)) return null;
	const names = new Set();
	for (const block of readFileSync(css, 'utf8').matchAll(/^@theme\b[^{\n]*\{([^}]*)\}/gm)) {
		for (const variable of block[1].matchAll(/--color-([a-z0-9-]+)\s*:/g)) names.add(variable[1]);
	}
	return names;
}

// The token of the same role as a raw colour: the answer of the message, and the mapping that
// ADR-0400 records. A shade of 600 and up is a solid fill of a button, a tab or a state, which
// the components own.
const SLATE_FILL = { 50: 'content', 100: 'content', 200: 'content', 300: 'content-soft', 400: 'content-secondary',
	500: 'status-neutral', 600: 'surface-hover', 700: 'surface-overlay', 800: 'surface-elevated', 900: 'surface',
	950: 'surface' };
const SLATE_TEXT = { 50: 'content', 100: 'content', 200: 'content', 300: 'content-soft', 400: 'content-secondary',
	500: 'content-muted', 600: 'content-muted', 700: 'content-muted', 800: 'content-inverse', 900: 'content-inverse',
	950: 'content-inverse' };
const SLATE_LINE = { 50: 'border-strong', 100: 'border-strong', 200: 'border-strong', 300: 'border-strong',
	400: 'border-strong', 500: 'border-strong', 600: 'border-strong', 700: 'border', 800: 'border', 900: 'border',
	950: 'border' };
const FAMILY_TOKEN = { red: 'status-error', green: 'status-success', emerald: 'status-success',
	yellow: 'status-warning', amber: 'status-warning', blue: 'status-info', sky: 'status-info',
	orange: 'accent-temperature', purple: 'accent-memory', violet: 'accent-memory', cyan: 'accent-container',
	teal: 'accent-container' };

/** The class to use instead of the raw colour `utility-family-shade`, as text for a message. */
function hint(utility, family, shade, white) {
	const kind = /^(?:text|fill|stroke|caret|decoration|placeholder|accent)$/.test(utility) ? 'text'
		: /^(?:border|ring|ring-offset|divide|outline)/.test(utility) ? 'line' : 'fill';
	if (white === 'black') return 'the `scrim` class for a backdrop, else a token of the @theme of src/app.css';
	if (white === 'white') return kind === 'text' ? `${utility}-content-on-brand` : 'a token of the @theme of src/app.css';
	const n = Number(shade);
	if (family === 'slate' || family === 'gray' || family === 'zinc' || family === 'neutral' || family === 'stone') {
		const table = kind === 'text' ? SLATE_TEXT : kind === 'line' ? SLATE_LINE : SLATE_FILL;
		const token = table[n];
		if (token === 'surface-overlay' && kind === 'line') return `${utility}-border`;
		return token ? `${utility}-${token}` : 'a token of the @theme of src/app.css';
	}
	if (family === 'blue' && n >= 500) return `${utility}-brand (brand-hover, brand-ring, brand-disabled)`;
	if (n >= 600 && FAMILY_TOKEN[family]) return 'a Button, Badge or Alert variant';
	const token = FAMILY_TOKEN[family];
	return token ? `${utility}-${token}` : 'a token of the @theme of src/app.css';
}

// --- What to look for ----------------------------------------------------------------------------

/** The raw colour classes of the text `value`, as { class, at (offset in value), message }. */
function rawColors(value) {
	const found = [];
	const words = /\S+/g;
	for (let match = words.exec(value); match; match = words.exec(value)) {
		const [, utility, family, shade, white] = COLOR.exec(match[0]) ?? [];
		if (!utility) continue;
		found.push({ name: match[0], at: match.index, use: hint(utility, family, shade, white) });
	}
	return found;
}

/** The class words of the text `value` that `pattern` matches, as { name, at }. */
function classesLike(value, pattern) {
	const found = [];
	const words = /\S+/g;
	for (let match = words.exec(value); match; match = words.exec(value)) {
		if (pattern.test(match[0])) found.push({ name: match[0], at: match.index });
	}
	return found;
}

/** Whether the TypeScript call `node` is window.confirm, alert or prompt, with the names the file declares. */
function nativeDialogCall(node, declared) {
	if (!ts.isCallExpression(node)) return null;
	const callee = node.expression;
	if (ts.isIdentifier(callee) && NATIVE_DIALOGS.has(callee.text) && !declared.has(callee.text)) return callee.text;
	if (ts.isPropertyAccessExpression(callee) && ts.isIdentifier(callee.expression) &&
		GLOBALS.has(callee.expression.text) && NATIVE_DIALOGS.has(callee.name.text)) {
		return `${callee.expression.text}.${callee.name.text}`;
	}
	return null;
}

/** The names that a TypeScript source declares: functions, variables, parameters and imports. */
function declaredNames(sourceFile) {
	const names = new Set();
	const visit = (node) => {
		if ((ts.isFunctionDeclaration(node) || ts.isVariableDeclaration(node) || ts.isParameter(node) ||
			ts.isImportSpecifier(node) || ts.isImportClause(node) || ts.isNamespaceImport(node)) &&
			node.name && ts.isIdentifier(node.name)) {
			names.add(node.name.text);
		}
		ts.forEachChild(node, visit);
	};
	visit(sourceFile);
	return names;
}

const DIALOG_HINT = 'use confirm() and toast of $lib/components/ui (ADR-0432)';

/** The source with the content of every <script> and <style> blanked out, and the scripts. */
function split(source) {
	const scripts = [];
	const blank = (text) => text.replace(/[^\n]/g, ' ');
	const markup = source
		.replace(/(<script\b[^>]*>)([\s\S]*?)(<\/script>)/g, (_, open, body, close, offset) => {
			scripts.push({ body, offset: offset + open.length });
			return open + blank(body) + close;
		})
		.replace(/(<style\b[^>]*>)([\s\S]*?)(<\/style>)/g, (_, open, body, close) => open + blank(body) + close);
	return { markup, scripts };
}

/** Whether an HTML comment `design-ignore: <reason>` stands right above `offset` of the markup. */
function markupExempted(markup, offset) {
	const before = markup.slice(0, offset).trimEnd();
	if (!before.endsWith('-->')) return false;
	const start = before.lastIndexOf('<!--');
	return start >= 0 && EXEMPTION.test(before.slice(start, -3));
}

/** Whether a comment `design-ignore: <reason>` stands above or after the statement or property of `node`. */
function scriptExempted(node, file) {
	const text = file.getFullText();
	for (let current = node; current.parent && !ts.isSourceFile(current); current = current.parent) {
		const { parent } = current;
		const member = ts.isSourceFile(parent) || ts.isBlock(parent) || ts.isCaseClause(parent) ||
			ts.isDefaultClause(parent) || ts.isLabeledStatement(parent) || ts.isObjectLiteralExpression(parent);
		if (!member) continue;
		const comments = [...(ts.getLeadingCommentRanges(text, current.pos) ?? []),
			...(ts.getTrailingCommentRanges(text, current.end) ?? [])];
		if (comments.some((range) => EXEMPTION.test(text.slice(range.pos, range.end).replace(/\*\/$/, '')))) return true;
	}
	return false;
}

/** The static text of the attribute `name` of an element node: '' when absent, null when not static. */
function staticAttribute(node, name) {
	const attribute = node.attributes.find((a) => a.type === 'Attribute' && a.name === name);
	if (!attribute) return '';
	if (attribute.value === true) return '';
	return attribute.value.every((part) => part.type === 'Text') ? attribute.value.map((part) => part.data).join('') : null;
}

/** Every class word of the attribute `class` (or `name`) of an element node: static texts and literals of its expressions. */
function classText(node, name = 'class') {
	const attribute = node.attributes.find((a) => a.type === 'Attribute' && a.name === name);
	if (!attribute || attribute.value === true) return '';
	const parts = [];
	for (const part of attribute.value) {
		if (part.type === 'Text') parts.push(part.data);
		else walk(part, { enter(inner) { if (inner.type === 'Literal' && typeof inner.value === 'string') parts.push(inner.value); } });
	}
	return parts.join(' ');
}

/** Whether an Alert node is a success one: the static variant "success", or a 'success' in its expression. */
function successAlert(node) {
	const variant = node.attributes.find((a) => a.type === 'Attribute' && a.name === 'variant');
	if (!variant || variant.value === true) return false;
	let success = false;
	for (const part of variant.value) {
		if (part.type === 'Text') success ||= part.data.trim() === 'success';
		else walk(part, { enter(inner) { if (inner.type === 'Literal' && inner.value === 'success') success = true; } });
	}
	return success;
}

/** Whether a Button node is a filled primary one: no variant, or the static variant "primary". */
function primaryButton(node) {
	const variant = node.attributes.find((a) => a.type === 'Attribute' && a.name === 'variant');
	if (!variant) return true;
	if (variant.value === true) return false;
	return variant.value.every((part) => part.type === 'Text') && variant.value.map((part) => part.data).join('') === 'primary';
}

const HEADINGS = new Set(['h3', 'h4', 'h5', 'h6']);

/** Whether an element node has a class word `word` in its static class text. */
function hasClass(node, word) {
	return node.type === 'Element' && new RegExp(`(^|\\s)${word}(\\s|$)`).test(classText(node));
}

/** Whether an element is a header row of a card: a justify-between box with a card's heading in it. */
function headerRow(node) {
	if (!hasClass(node, 'justify-between')) return false;
	const heading = (child) => child.type === 'Element' && (HEADINGS.has(child.name) || hasClass(child, 'card-title'));
	return (node.children ?? []).some((child) => heading(child) ||
		(child.type === 'Element' && (child.children ?? []).some(heading)));
}

/** Where a Button sits that may not be primary: a card's header or a table's row; null elsewhere. */
function cardOrRow(ancestors, button) {
	for (let i = ancestors.length - 1; i >= 0; i--) {
		const node = ancestors[i];
		if (node.type === 'InlineComponent' && /^(Modal|Sheet|ConfirmDialog)$/.test(node.name)) return null;
		if (node.type === 'SnippetBlock' && node.expression?.name === 'actions' && ancestors[i - 1]?.type === 'InlineComponent' &&
			ancestors[i - 1].name === 'Card') return "a card's header (Card actions)";
		if (node.type !== 'Element') continue;
		if (node.name === 'tr' || node.name === 'td' || hasClass(node, 'table-actions')) return "a table's row";
		if (hasClass(node, 'card-header') || headerRow(node)) return "a card's header";
		if (hasClass(node, 'page-header') || hasClass(node, 'modal-footer')) return null;
	}
	void button;
	return null;
}

// --- The check -----------------------------------------------------------------------------------

function checkFile(file) {
	const name = relative(file);
	const source = readFileSync(file, 'utf8');
	const { markup, scripts } = split(source);
	const report = (offset, text) => errors.push(`${name}:${position(source, offset)}: ${text}`);
	const color = (offset, value, exempted) => {
		for (const found of rawColors(value)) {
			if (exempted()) exempt += 1;
			else report(offset + found.at, `raw colour class '${found.name}': use ${found.use}`);
		}
		for (const found of classesLike(value, UPPERCASE)) {
			if (exempted()) exempt += 1;
			else report(offset + found.at, `class '${found.name}': a badge is sentence case, a group label the class eyebrow (ADR-0432)`);
		}
		for (const found of classesLike(value, SPINNER)) {
			if (exempted()) exempt += 1;
			else report(offset + found.at, `class '${found.name}': a hand-made spinner; use Button's loading, LoadState or Skeleton of $lib/components/ui, a pulsing StatusDot for a job that runs (ADR-0438)`);
		}
	};
	// The scripts' declared names: a component's own `confirm` function is no browser dialog.
	const declared = new Set();
	for (const script of scripts) {
		for (const name of declaredNames(ts.createSourceFile('script.ts', script.body, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS))) {
			declared.add(name);
		}
	}

	let ast;
	try {
		ast = parse(markup, { filename: name });
	} catch (error) {
		errors.push(`${name}:${error.start ? `${error.start.line}:${error.start.column + 1}` : '1:1'}: cannot be parsed: ${error.message}`);
		return;
	}

	const ancestors = [];
	/** Whether a design-ignore comment stands above the nearest tag (or block) around the node. */
	const exemptedHere = () => {
		for (let i = ancestors.length - 1; i >= 0; i--) {
			if (markupExempted(markup, ancestors[i].start)) return true;
			if (TAG_LIKE.has(ancestors[i].type)) return false;
		}
		return false;
	};

	walk(ast.html, {
		enter(node) {
			if (node.type === 'Element') {
				const own = () => markupExempted(markup, node.start);
				const tag = (text) => {
					if (own()) exempt += 1;
					else report(node.start, text);
				};
				if (node.name === 'button') tag('raw <button>: use Button of $lib/components/ui (variant="plain" for a tab or a clickable card)');
				else if (node.name === 'details') tag('raw <details>: use TechDetails of $lib/components/ui (ADR-0438)');
				else if (node.name === 'a' && /(^|\s)btn(-|\s|$)/.test(classText(node))) {
					tag('raw <a> styled as a button: use Button with href');
				} else if (node.name === 'input' || node.name === 'select' || node.name === 'textarea') {
					const type = node.name === 'input' ? staticAttribute(node, 'type') : '';
					if (type !== null && CHOICE_TYPES.has(type)) {
						tag(`raw <input type="${type}">: use ${type === 'radio' ? 'RadioGroup' : 'Checkbox or Switch'} of $lib/components/ui (ADR-0432)`);
					} else if (type === null || FIELD_TYPES_EXEMPT.has(type)) {
						// A type that is not static, or one that no component covers.
					} else {
						const classes = classText(node).split(/\s+/);
						const own = node.name === 'select' ? 'form-select' : 'form-input';
						const other = node.name === 'select' ? 'form-input' : 'form-select';
						const appearance = classes.find((word) => APPEARANCE.test(word)) ??
							(node.attributes.some((a) => a.type === 'StyleDirective' && /appearance$/.test(a.name)) ||
							/appearance\s*:/.test(classText(node, 'style')) ? 'a style that sets appearance' : null);
						if (classes.includes(other) && !classes.includes(own)) {
							tag(`raw <${node.name}> with the class ${other}: ${node.name === 'input' ? 'an' : 'a'} <${node.name}> takes ${own}, which draws it in every browser`);
						} else if (!classes.includes(own)) {
							tag(`raw <${node.name}> without the class ${own}: use ${node.name === 'select' ? 'Select' : 'Input'} of $lib/components/ui, or the class ${own}`);
						} else if (appearance) {
							tag(`raw <${node.name}> with ${appearance.startsWith('a style') ? appearance : `the class '${appearance}'`}: ${own} sets its appearance, so that Safari draws it as Chromium does`);
						}
					}
				}
			} else if (node.type === 'InlineComponent' && node.name === 'Button' && primaryButton(node)) {
				const where = cardOrRow(ancestors, node);
				if (where) {
					if (markupExempted(markup, node.start)) exempt += 1;
					else report(node.start, `a filled primary Button in ${where}: use CardAction, a secondary or ghost Button or an ActionMenu; the primary action is the page header's (ADR-0432)`);
				}
			} else if (node.type === 'InlineComponent' && node.name === 'Alert' && successAlert(node)) {
				if (markupExempted(markup, node.start)) exempt += 1;
				else report(node.start, 'a success Alert: what an action did is a toast (toast.success of $lib/components/ui); an outcome ' +
					'to read before going on keeps its Alert with `design-ignore: <reason>` (ADR-0438)');
			} else if (node.type === 'CallExpression') {
				const callee = node.callee;
				const called = callee?.type === 'Identifier' && NATIVE_DIALOGS.has(callee.name) && !declared.has(callee.name)
					? callee.name
					: callee?.type === 'MemberExpression' && callee.object?.type === 'Identifier' && GLOBALS.has(callee.object.name) &&
						callee.property?.type === 'Identifier' && NATIVE_DIALOGS.has(callee.property.name)
						? `${callee.object.name}.${callee.property.name}` : null;
				if (called) {
					if (exemptedHere()) exempt += 1;
					else report(node.start, `the browser's ${called}(): ${DIALOG_HINT}`);
				}
			} else if (node.type === 'Class') {
				color(node.start, node.name, () => ancestors.some((a) => markupExempted(markup, a.start)) && exemptedHere());
			} else if (node.type === 'Text' && ancestors.at(-1)?.type === 'Attribute' && ancestors.at(-1).name === 'class') {
				color(node.start, node.data, exemptedHere);
			} else if (node.type === 'Literal' && typeof node.value === 'string') {
				color(node.start + 1, node.value, exemptedHere);
			} else if (node.type === 'TemplateElement') {
				color(node.start, node.value.cooked ?? '', exemptedHere);
			}
			ancestors.push(node);
		},
		leave() {
			ancestors.pop();
		}
	});

	for (const script of scripts) {
		const sourceFile = ts.createSourceFile('script.ts', script.body, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
		const visit = (node) => {
			const called = nativeDialogCall(node, declared);
			if (called) {
				if (scriptExempted(node, sourceFile)) exempt += 1;
				else report(script.offset + node.getStart(sourceFile), `the browser's ${called}(): ${DIALOG_HINT}`);
			}
			if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) || ts.isTemplateHead(node) ||
				ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) {
				color(script.offset + node.getStart(sourceFile) + 1, node.text, () => scriptExempted(node, sourceFile));
			}
			ts.forEachChild(node, visit);
		};
		visit(sourceFile);
	}
}

/** The names of every colour token that the table above can answer with, checked against the config. */
function checkTable() {
	const tokens = tokenNames();
	if (tokens === null) return;
	const named = new Set([...Object.values(SLATE_FILL), ...Object.values(SLATE_TEXT), ...Object.values(SLATE_LINE),
		...Object.values(FAMILY_TOKEN), 'content-on-brand', 'brand', 'brand-hover', 'brand-ring', 'brand-disabled']);
	for (const token of named) {
		if (!tokens.has(token)) errors.push(`scripts/design-check.mjs: the table names the token '${token}', which the @theme of src/app.css lacks`);
	}
}

/** The browser's dialogs in a .ts module outside ui. */
function checkModule(file) {
	const name = relative(file);
	const text = readFileSync(file, 'utf8');
	const sourceFile = ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
	const declared = declaredNames(sourceFile);
	const visit = (node) => {
		const called = nativeDialogCall(node, declared);
		if (called) {
			if (scriptExempted(node, sourceFile)) exempt += 1;
			else errors.push(`${name}:${position(text, node.getStart(sourceFile))}: the browser's ${called}(): ${DIALOG_HINT}`);
		}
		ts.forEachChild(node, visit);
	};
	visit(sourceFile);
}

// --- Numbers and dates (ADR-0459) ----------------------------------------------------------------

// The one home of the formats: every other file of src/, ui's included, writes its numbers and dates
// with them.
const FORMAT_HOME = new Set([path.join(SRC, 'lib', 'format.ts'), path.join(SRC, 'lib', 'time.ts')]);
const CATALOGS = path.join(SRC, 'lib', 'i18n', 'catalog') + path.sep;
const FORMAT_METHODS = new Set(['toFixed', 'toPrecision', 'toLocaleString', 'toLocaleDateString', 'toLocaleTimeString',
	'toDateString', 'toTimeString', 'toUTCString']);
// A percentage or a unit of a size written right after a value: ' %', 'MB', ' GB/s'.
const UNIT_AFTER = /^\s?(?:%|(?:B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)(?:\/s)?)(?![A-Za-z])/;
const UNIT_FOUND = 'a percentage or a unit put together after a value'
const DATE_GETTERS = /^get(?:UTC)?(?:FullYear|Year|Month|Date|Day|Hours|Minutes|Seconds)$/;
const INTL_FORMATS = new Set(['NumberFormat', 'DateTimeFormat', 'RelativeTimeFormat', 'DurationFormat']);
const FORMAT_HINT = 'use the functions of $lib/format and $lib/time (ADR-0459)';

/**
 * What is made by hand in a call or `new` of `method` on `object` (names), with its arguments and
 * whether its receiver is a number written in another base (`n.toString(16)`, a hex byte of a MAC);
 * null for nothing.
 */
function handMadeFormat(object, method, args, radix = false) {
	if (object === 'Intl' && INTL_FORMATS.has(method)) return `Intl.${method}`;
	if (FORMAT_METHODS.has(method)) return `${method}()`;
	if (DATE_GETTERS.test(method)) return `a date's ${method}()`;
	if (method === 'padStart' && args[0] === 2 && args[1] === '0' && !radix) return "padStart(2, '0') of a date's or a time's field";
	return null;
}

/** The value of a literal argument (a TypeScript or an ESTree node); undefined for another. */
const literalOf = (node) => node?.type === 'Literal' ? node.value
	: node?.kind === undefined ? undefined
		: ts.isNumericLiteral(node) ? Number(node.text)
			: ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) ? node.text : undefined;

/** The hand-made formats of a TypeScript source, reported at `offset` plus their position. */
function tsFormats(sourceFile, offset, report) {
	const visit = (node) => {
		if ((ts.isCallExpression(node) || ts.isNewExpression(node)) && ts.isPropertyAccessExpression(node.expression)) {
			const callee = node.expression;
			const object = ts.isIdentifier(callee.expression) ? callee.expression.text : '';
			const inner = callee.expression;
			const radix = ts.isCallExpression(inner) && ts.isPropertyAccessExpression(inner.expression) &&
				inner.expression.name.text === 'toString' && inner.arguments.length > 0;
			const found = handMadeFormat(object, callee.name.text, (node.arguments ?? []).map(literalOf), radix);
			if (found) {
				if (scriptExempted(node, sourceFile)) exempt += 1;
				else report(offset + node.getStart(sourceFile), found);
			}
		}
		// A unit after a value: value + ' MB', or `${value}%` (a template's text after a substitution).
		const units = [];
		if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.PlusToken && ts.isStringLiteral(node.right) &&
			!ts.isStringLiteral(node.left) && UNIT_AFTER.test(node.right.text)) units.push(node.right);
		if (ts.isTemplateExpression(node)) units.push(...node.templateSpans.map((span) => span.literal).filter((text) => UNIT_AFTER.test(text.text)));
		for (const unit of units) {
			if (scriptExempted(node, sourceFile)) exempt += 1;
			else report(offset + unit.getStart(sourceFile), UNIT_FOUND);
		}
		ts.forEachChild(node, visit);
	};
	visit(sourceFile);
}

/** The hand-made formats of a .svelte file: in its scripts, its markup's expressions and a `{value}%` of its text. */
function checkFileFormats(file) {
	const name = relative(file);
	const source = readFileSync(file, 'utf8');
	const { markup, scripts } = split(source);
	const report = (offset, found) => errors.push(`${name}:${position(source, offset)}: ${found} writes a number or a date by hand: ${FORMAT_HINT}`);
	for (const script of scripts) {
		tsFormats(ts.createSourceFile('script.ts', script.body, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS), script.offset, report);
	}
	let ast;
	try {
		ast = parse(markup, { filename: name });
	} catch {
		return; // checkFile reports it, or svelte-check does for a file of ui.
	}
	const ancestors = [];
	const exemptedHere = () => {
		for (let i = ancestors.length - 1; i >= 0; i--) {
			if (markupExempted(markup, ancestors[i].start)) return true;
			if (TAG_LIKE.has(ancestors[i].type)) return false;
		}
		return false;
	};
	walk(ast.html, {
		enter(node) {
			const callee = node.callee;
			if ((node.type === 'CallExpression' || node.type === 'NewExpression') && callee?.type === 'MemberExpression' &&
				callee.property?.type === 'Identifier') {
				const object = callee.object?.type === 'Identifier' ? callee.object.name : '';
				const inner = callee.object;
				const radix = inner?.type === 'CallExpression' && inner.callee?.type === 'MemberExpression' &&
					inner.callee.property?.name === 'toString' && inner.arguments.length > 0;
				const found = handMadeFormat(object, callee.property.name, node.arguments.map(literalOf), radix);
				if (found) {
					if (exemptedHere()) exempt += 1;
					else report(node.start, found);
				}
			}
			// A unit after a value in an expression: {value + '%'}, {`${value} MB`}.
			const units = [];
			if (node.type === 'BinaryExpression' && node.operator === '+' && node.right?.type === 'Literal' &&
				typeof node.right.value === 'string' && node.left?.type !== 'Literal' && UNIT_AFTER.test(node.right.value)) units.push(node.right);
			if (node.type === 'TemplateLiteral') units.push(...node.quasis.slice(1).filter((quasi) => UNIT_AFTER.test(quasi.value.cooked ?? '')));
			for (const unit of units) {
				if (exemptedHere()) exempt += 1;
				else report(unit.start, UNIT_FOUND);
			}
			// A unit after a value in the markup's text: {value}% or {value} MB between tags, or in an
			// attribute other than a style (title="{value}%"), whose width: {value}% is CSS.
			const sequences = [node.children ?? []];
			if (node.type === 'Attribute' && node.name !== 'style' && Array.isArray(node.value)) sequences.push(node.value);
			for (const parts of sequences) {
				parts.forEach((part, index) => {
					const next = parts[index + 1];
					if ((part.type === 'MustacheTag' || part.type === 'AttributeShorthand') && next?.type === 'Text' && UNIT_AFTER.test(next.data)) {
						if (markupExempted(markup, node.start) || exemptedHere()) exempt += 1;
						else report(next.start, `${UNIT_FOUND} ({value}%, {value} MB)`);
					}
				});
			}
			ancestors.push(node);
		},
		leave() {
			ancestors.pop();
		}
	});
}

/** The hand-made formats of a .ts module; in a catalog, a percentage put together in a text. */
function checkModuleFormats(file) {
	const name = relative(file);
	const text = readFileSync(file, 'utf8');
	const sourceFile = ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
	const report = (offset, found) => errors.push(`${name}:${position(text, offset)}: ${found} writes a number or a date by hand: ${FORMAT_HINT}`);
	tsFormats(sourceFile, 0, report);
	if (!file.startsWith(CATALOGS)) return;
	// The texts of the catalog's halves: a placeholder before % is a percentage put together in a text
	// (pass formatPercent's in a placeholder), and Russian writes a no-break space before % (80 %), as
	// formatPercent does.
	const visit = (node, half) => {
		if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name)) half = node.name.text;
		if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
			const key = ts.isPropertyAssignment(node.parent) && node.parent.name === node;
			const where = text.slice(0, node.getStart(sourceFile)).split('\n').length;
			if (!key && /\{\w+\}\s?%/.test(node.text)) {
				errors.push(`${name}:${where}: a percentage put together in a text ({value}%): pass formatPercent's of $lib/format in the placeholder (ADR-0459)`);
			} else if (!key && half === 'ru' && /\d ?%/.test(node.text)) {
				errors.push(`${name}:${where}: a Russian percentage without the no-break space before % (write 80\\u00a0%, as formatPercent of $lib/format does) (ADR-0459)`);
			}
		}
		ts.forEachChild(node, (child) => visit(child, half));
	};
	visit(sourceFile, '');
}

const files = readdirSync(SRC, { recursive: true })
	.filter((entry) => entry.endsWith('.svelte'))
	.map((entry) => path.join(SRC, entry))
	.filter((file) => !file.startsWith(UI))
	.sort();
for (const file of files) checkFile(file);
const modules = readdirSync(SRC, { recursive: true })
	.filter((entry) => entry.endsWith('.ts') && !entry.endsWith('.d.ts'))
	.map((entry) => path.join(SRC, entry))
	.filter((file) => !file.startsWith(UI))
	.sort();
for (const file of modules) checkModule(file);
const formatted = readdirSync(SRC, { recursive: true })
	.filter((entry) => entry.endsWith('.svelte') || (entry.endsWith('.ts') && !entry.endsWith('.d.ts')))
	.map((entry) => path.join(SRC, entry))
	.filter((file) => !FORMAT_HOME.has(file))
	.sort();
for (const file of formatted) {
	if (file.endsWith('.svelte')) checkFileFormats(file);
	else checkModuleFormats(file);
}
checkTable();

if (errors.length > 0) {
	for (const error of errors) console.error(error);
	console.error(`design: ${errors.length} problem(s). Pages use the components of $lib/components/ui and the colour ` +
		'tokens of the @theme of src/app.css (webui/AGENTS.md, ADR-0400); `design-ignore: <reason>` exempts a use.');
	process.exit(1);
}
console.log(`design: ${files.length} .svelte files outside lib/components/ui use no raw <button>, choice, ` +
	'<details> or bare field, no field off the class of its kind, no raw colour class, no browser dialog, no uppercase, no primary button in a card\'s header ' +
	`or a row, no success Alert and no hand-made spinner; ${formatted.length} files of src/ write no number or date by ` +
	`hand; ${exempt} use(s) exempted with design-ignore`);
