#!/usr/bin/env node
// Pages are built from the design system: the components of src/lib/components/ui and the colour
// tokens of tailwind.config.js (ADR-0012, ADR-0400). make frontend runs this check (npm run
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
// - a raw Tailwind colour: a utility of the palette (bg-slate-700, text-red-400, hover:bg-blue-600,
//   border-green-500/50, ring-white) in a class attribute, a `class:` directive or any string
//   literal of the markup or a <script>. Use the token of the same role (the message names it);
//   a colour that no token covers is added to tailwind.config.js first.
// A comment `design-ignore: <reason>` exempts what it stands for: above an element (an HTML comment
// <!-- design-ignore: ... -->), or above or at the end of the statement or property of a script
// (as `i18n-ignore:` does). Review reads the reasons; the summary counts the exemptions.

import { existsSync, readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
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

/** The class names of the colour tokens of tailwind.config.js ('surface-overlay', 'status-error'). */
async function tokenNames() {
	const config = path.join(webui, 'tailwind.config.js');
	if (!existsSync(config)) return null;
	const colors = (await import(pathToFileURL(config).href)).default?.theme?.extend?.colors ?? {};
	const names = new Set();
	const add = (prefix, value) => {
		if (typeof value === 'string') {
			names.add(prefix);
			return;
		}
		for (const [key, inner] of Object.entries(value)) add(key === 'DEFAULT' ? prefix : `${prefix}-${key}`, inner);
	};
	for (const [key, value] of Object.entries(colors)) add(key, value);
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
	if (white === 'black') return 'the `scrim` class for a backdrop, else a token of tailwind.config.js';
	if (white === 'white') return kind === 'text' ? `${utility}-content-on-brand` : 'a token of tailwind.config.js';
	const n = Number(shade);
	if (family === 'slate' || family === 'gray' || family === 'zinc' || family === 'neutral' || family === 'stone') {
		const table = kind === 'text' ? SLATE_TEXT : kind === 'line' ? SLATE_LINE : SLATE_FILL;
		const token = table[n];
		if (token === 'surface-overlay' && kind === 'line') return `${utility}-border`;
		return token ? `${utility}-${token}` : 'a token of tailwind.config.js';
	}
	if (family === 'blue' && n >= 500) return `${utility}-brand (brand-hover, brand-ring, brand-disabled)`;
	if (n >= 600 && FAMILY_TOKEN[family]) return 'a Button, Badge or Alert variant';
	const token = FAMILY_TOKEN[family];
	return token ? `${utility}-${token}` : 'a token of tailwind.config.js';
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

/** Every class word of the attribute `class` of an element node: static texts and literals of its expressions. */
function classText(node) {
	const attribute = node.attributes.find((a) => a.type === 'Attribute' && a.name === 'class');
	if (!attribute || attribute.value === true) return '';
	const parts = [];
	for (const part of attribute.value) {
		if (part.type === 'Text') parts.push(part.data);
		else walk(part, { enter(inner) { if (inner.type === 'Literal' && typeof inner.value === 'string') parts.push(inner.value); } });
	}
	return parts.join(' ');
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
	};

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
				else if (node.name === 'a' && /(^|\s)btn(-|\s|$)/.test(classText(node))) {
					tag('raw <a> styled as a button: use Button with href');
				} else if (node.name === 'input' || node.name === 'select' || node.name === 'textarea') {
					const type = node.name === 'input' ? staticAttribute(node, 'type') : '';
					if (type === null || FIELD_TYPES_EXEMPT.has(type)) {
						// A type that is not static, or one that no component covers.
					} else if (!/(^|\s)form-(?:input|select|textarea)(\s|$)/.test(classText(node))) {
						tag(`raw <${node.name}> without a form-* class: use ${node.name === 'select' ? 'Select' : 'Input'} of $lib/components/ui, or the class form-${node.name === 'select' ? 'select' : 'input'}`);
					}
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
async function checkTable() {
	const tokens = await tokenNames();
	if (tokens === null) return;
	const named = new Set([...Object.values(SLATE_FILL), ...Object.values(SLATE_TEXT), ...Object.values(SLATE_LINE),
		...Object.values(FAMILY_TOKEN), 'content-on-brand', 'brand', 'brand-hover', 'brand-ring', 'brand-disabled']);
	for (const token of named) {
		if (!tokens.has(token)) errors.push(`scripts/design-check.mjs: the table names the token '${token}', which tailwind.config.js lacks`);
	}
}

const files = readdirSync(SRC, { recursive: true })
	.filter((entry) => entry.endsWith('.svelte'))
	.map((entry) => path.join(SRC, entry))
	.filter((file) => !file.startsWith(UI))
	.sort();
for (const file of files) checkFile(file);
await checkTable();

if (errors.length > 0) {
	for (const error of errors) console.error(error);
	console.error(`design: ${errors.length} problem(s). Pages use the components of $lib/components/ui and the colour ` +
		'tokens of tailwind.config.js (webui/AGENTS.md, ADR-0400); `design-ignore: <reason>` exempts a use.');
	process.exit(1);
}
console.log(`design: ${files.length} .svelte files outside lib/components/ui use no raw <button>, no bare field and ` +
	`no raw colour class; ${exempt} use(s) exempted with design-ignore`);
