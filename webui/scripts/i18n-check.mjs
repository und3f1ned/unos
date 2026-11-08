#!/usr/bin/env node
// Every text of the web UI comes from a catalog with English and Russian (ADR-0273). make frontend
// runs this check (npm run i18n); supervisor/tests/test_i18n.py tests it.
//
//   node scripts/i18n-check.mjs [webui directory]      (default: the webui/ of this script)
//
// 1. The catalogs, src/lib/i18n/catalog/<area>.ts: each exports `en` and `ru` with the same keys,
//    every key starts with "<area>.", no text is empty, a text with plural forms has the forms
//    that whole numbers take in its language (Intl.PluralRules: one and other in English; one,
//    few and many in Russian), and both languages have the same {placeholders}.
// 2. Every .svelte file under src/ shows no text with letters that does not come from $t(...):
//    - in the markup, no text between tags, no such text in a user-facing attribute or prop
//      (placeholder, title, alt, aria-label, label, hint, description, error, a prop named
//      ...Label, ...Hint, ...Text and the like), and no string literal that an expression of the
//      markup shows, as in {busy ? 'Saving...' : x} or {port || 'Any'};
//    - in the <script>, no string literal that goes to a variable, prop or property whose name
//      says that it holds a text (error = 'Failed', label: 'Storage', REFUSED_TITLE = '...',
//      export let title = 'No data'), or to confirm(), alert() or prompt().
//    A text that the script shows in another way (a function's return value) is out of its
//    reach: it comes from $t as well, and review checks it.
// 3. Every key that a $t('...') call names exists in English, and a text with plural forms gets
//    its count: $t('area.key', { n }).
// 4. scripts/i18n-untranslated.txt lists the .svelte files not translated yet. Their texts are
//    not reported, and a listed file without such texts fails until it leaves the list.

import { existsSync, readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse, walk } from 'svelte/compiler';
import ts from 'typescript';

const LOCALES = ['en', 'ru'];
const LETTER = /\p{L}/u;
const PLACEHOLDER = /\{(\w+)\}/g;
// Attributes and props whose value people read (or hear, from a screen reader).
const TEXT_ATTRIBUTE =
	/^(?:placeholder|title|alt|label|hint|description|error|message|text|tooltip|aria-(?:label|description|roledescription|valuetext|placeholder))$|(?:Label|Hint|Title|Text|Message|Description|Placeholder|Tooltip)$/;
// Directives: code, not texts.
const NOT_TEXT = new Set(['Comment', 'EventHandler', 'Binding', 'Action', 'Transition', 'Animation', 'Class',
	'StyleDirective', 'Let', 'Spread']);
// A variable of a script (or a property set by assignment) whose name says that it holds a text.
const TEXT_VARIABLE =
	/^(?:label|title|text|message|description|hint|placeholder|tooltip|caption|heading|error|notice|warning|success)$|[a-z](?:Label|Title|Text|Message|Description|Hint|Placeholder|Tooltip|Caption|Heading|Error|Notice|Warning|Success)$|^(?:[A-Z0-9]+_)*(?:LABEL|TITLE|TEXT|MESSAGE|DESCRIPTION|HINT|NOTE|WARNING|ERROR|NOTICE|CAPTION|HEADING)$/;
// The same for a property of an object literal, without the words that also name a variant
// ({ error: 'alert-error', success: 'badge-success' }).
const TEXT_PROPERTY =
	/^(?:label|title|text|message|description|hint|placeholder|tooltip|caption|heading)$|[a-z](?:Label|Title|Text|Message|Description|Hint|Placeholder|Tooltip|Caption|Heading)$/;
// The browser's dialogs show their first argument.
const DIALOGS = new Set(['confirm', 'alert', 'prompt']);
// An element id or a class (task-time-zone-hint) is not a text.
const IDENTIFIER = /^[a-z0-9]+(?:-[a-z0-9]+)+$/;
const SHOWN_OPERATORS = new Set([ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken,
	ts.SyntaxKind.AmpersandAmpersandToken, ts.SyntaxKind.PlusToken]);

const webui = path.resolve(process.argv[2] ?? path.join(path.dirname(fileURLToPath(import.meta.url)), '..'));
const SRC = path.join(webui, 'src');
const CATALOG_DIR = path.join(webui, 'src', 'lib', 'i18n', 'catalog');
const LIST = path.join(webui, 'scripts', 'i18n-untranslated.txt');

const errors = [];
const relative = (file) => path.relative(webui, file).split(path.sep).join('/');

/** "line:column" of `offset` in `text`, both counted from 1. */
function position(text, offset) {
	const before = text.slice(0, offset).split('\n');
	return `${before.length}:${before[before.length - 1].length + 1}`;
}

/** A text as a report quotes it: on one line, cut after 60 characters. */
function quote(text) {
	const line = text.replace(/\s+/g, ' ').trim();
	return JSON.stringify(line.length > 60 ? `${line.slice(0, 57)}...` : line);
}

const isObject = (value) => typeof value === 'object' && value !== null && !Array.isArray(value);
const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
/** 'text', 'plural', or null for a value that is neither (checkHalf reports it). */
const kind = (message) => (typeof message === 'string' ? 'text' : isObject(message) ? 'plural' : null);

// --- 1. The catalogs ---------------------------------------------------------------------------

/** The plural categories that whole numbers take in `locale`, sorted. */
function pluralForms(locale) {
	const rules = new Intl.PluralRules(locale);
	const forms = new Set();
	for (let n = 0; n <= 1000; n++) forms.add(rules.select(n));
	return [...forms].sort();
}
const PLURAL_FORMS = Object.fromEntries(LOCALES.map((locale) => [locale, pluralForms(locale)]));

/** The {placeholders} of a text, or of all forms of a plural text, sorted. */
function placeholders(message) {
	const texts = typeof message === 'string' ? [message] : Object.values(message);
	return [...new Set(texts.flatMap((text) => [...String(text).matchAll(PLACEHOLDER)].map((match) => match[1])))].sort();
}

/** The module of a catalog. Catalogs hold data only: a runtime import fails here. */
async function loadCatalog(file) {
	const { outputText } = ts.transpileModule(readFileSync(file, 'utf8'), {
		compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
		fileName: file
	});
	return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
}

/** Check one half of a catalog; reports what is wrong with it. */
function checkHalf(where, area, locale, messages) {
	for (const [key, message] of Object.entries(messages)) {
		if (!key.startsWith(`${area}.`)) errors.push(`${where}: ${key}: a key of this catalog starts with "${area}."`);
		if (typeof message === 'string') {
			if (!message.trim()) errors.push(`${where}: ${key} (${locale}): an empty text`);
		} else if (isObject(message)) {
			const forms = Object.keys(message).sort();
			if (forms.join() !== PLURAL_FORMS[locale].join()) {
				errors.push(`${where}: ${key} (${locale}): plural forms ${forms.join(', ')}; ${locale} has ` +
					`${PLURAL_FORMS[locale].join(', ')}`);
			}
			for (const form of forms) {
				if (typeof message[form] !== 'string' || !message[form].trim()) {
					errors.push(`${where}: ${key} (${locale}): the ${form} form is not a text`);
				}
			}
		} else {
			errors.push(`${where}: ${key} (${locale}): neither a text nor plural forms`);
		}
	}
}

/** Check every catalog; returns the English texts of all of them, by key. */
async function checkCatalogs() {
	const english = new Map();
	const files = existsSync(CATALOG_DIR)
		? readdirSync(CATALOG_DIR).filter((name) => name.endsWith('.ts')).sort()
		: [];
	if (files.length === 0) errors.push(`${relative(CATALOG_DIR)}: no catalogs`);
	for (const name of files) {
		const file = path.join(CATALOG_DIR, name);
		const where = relative(file);
		const area = name.slice(0, -'.ts'.length);
		let catalog;
		try {
			catalog = await loadCatalog(file);
		} catch (error) {
			errors.push(`${where}: cannot be loaded (a catalog imports no values): ${error.message}`);
			continue;
		}
		const halves = LOCALES.filter((locale) => {
			if (isObject(catalog[locale])) return true;
			errors.push(`${where}: exports no object ${locale}`);
			return false;
		});
		if (halves.length !== LOCALES.length) continue;
		const { en, ru } = catalog;
		for (const locale of LOCALES) checkHalf(where, area, locale, catalog[locale]);
		for (const key of Object.keys(en)) {
			if (english.has(key)) errors.push(`${where}: ${key}: also in ${english.get(key).where}`);
			english.set(key, { where, message: en[key] });
			if (!own(ru, key)) {
				errors.push(`${where}: ${key}: missing in ru`);
				continue;
			}
			const [enKind, ruKind] = [kind(en[key]), kind(ru[key])];
			if (enKind !== ruKind) {
				if (enKind && ruKind) errors.push(`${where}: ${key}: a text in one language and plural forms in the other`);
			} else if (enKind) {
				const [enNames, ruNames] = [placeholders(en[key]), placeholders(ru[key])];
				if (enNames.join() !== ruNames.join()) {
					errors.push(`${where}: ${key}: placeholders {${enNames.join('}, {')}} in en, ` +
						`{${ruNames.join('}, {')}} in ru`);
				}
			}
		}
		for (const key of Object.keys(ru)) {
			if (!own(en, key)) errors.push(`${where}: ${key}: in ru but not in en`);
		}
	}
	return english;
}

// --- 2. and 3. The .svelte files -----------------------------------------------------------------

function svelteFiles() {
	return readdirSync(SRC, { recursive: true })
		.filter((name) => name.endsWith('.svelte'))
		.map((name) => path.join(SRC, name))
		.sort();
}

/**
 * The source with the content of every <script> and <style> blanked out (lines and offsets stay),
 * and the scripts' content with its offset. Svelte's parser reads the markup; TypeScript reads the
 * scripts.
 */
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

const isT = (callee) => callee?.type === 'Identifier' && callee.name === '$t';

/** The string literals with letters that `node`, an expression of the markup, can show. */
function shownLiterals(node) {
	if (!node) return [];
	switch (node.type) {
		case 'Literal':
			return typeof node.value === 'string' && LETTER.test(node.value) ? [{ start: node.start, text: node.value }] : [];
		case 'TemplateLiteral':
			return [
				...node.quasis
					.filter((quasi) => LETTER.test(quasi.value.cooked ?? quasi.value.raw))
					.map((quasi) => ({ start: quasi.start, text: quasi.value.cooked ?? quasi.value.raw })),
				...node.expressions.flatMap(shownLiterals)
			];
		case 'ConditionalExpression':
			return [...shownLiterals(node.consequent), ...shownLiterals(node.alternate)];
		case 'LogicalExpression':
			return [...shownLiterals(node.left), ...shownLiterals(node.right)];
		case 'BinaryExpression':
			return node.operator === '+' ? [...shownLiterals(node.left), ...shownLiterals(node.right)] : [];
		case 'SequenceExpression':
			return shownLiterals(node.expressions[node.expressions.length - 1]);
		case 'ArrayExpression':
			return node.elements.flatMap(shownLiterals);
		case 'ChainExpression':
			return shownLiterals(node.expression);
		case 'CallExpression':
			if (isT(node.callee)) return [];
			// A method of a text, as in ('None' || x).toUpperCase(), shows what it is called on.
			return node.callee.type === 'MemberExpression' ? shownLiterals(node.callee.object) : [];
		default:
			return [];
	}
}

/** Whether `attribute` of `owner` (an element or a component) is read by people. */
function isTextAttribute(attribute, owner) {
	if (TEXT_ATTRIBUTE.test(attribute.name)) return true;
	// The value of a button made of an <input> is its label.
	if (attribute.name !== 'value' || owner?.type !== 'Element' || owner.name !== 'input') return false;
	const type = owner.attributes.find((other) => other.type === 'Attribute' && other.name === 'type');
	return Array.isArray(type?.value) && type.value.length === 1 && ['submit', 'button', 'reset'].includes(type.value[0].data);
}

/** A text node of the markup, from its first letter on, when it has letters. */
function textNode(node) {
	if (!LETTER.test(node.data)) return [];
	const raw = node.raw ?? node.data;
	return [{ start: node.start + raw.length - raw.trimStart().length, text: node.data }];
}

/** The untranslated texts of the markup: [{ start, text }]. */
function markupTexts(html) {
	const found = [];
	const literals = (expression) => found.push(...shownLiterals(expression));
	walk(html, {
		enter(node, parent) {
			if (NOT_TEXT.has(node.type)) {
				this.skip();
			} else if (node.type === 'Attribute') {
				if (isTextAttribute(node, parent) && Array.isArray(node.value)) {
					for (const part of node.value) {
						if (part.type === 'Text') found.push(...textNode(part));
						if (part.type === 'MustacheTag') literals(part.expression);
					}
				}
				this.skip();
			} else if (node.type === 'Text') {
				found.push(...textNode(node));
			} else if (node.type === 'MustacheTag' || node.type === 'RawMustacheTag') {
				literals(node.expression);
				this.skip();
			}
		}
	});
	return found;
}

/** The string literals with letters that `node`, an expression of a script, can yield as a text. */
function shownStrings(node) {
	if (!node) return [];
	if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
		return LETTER.test(node.text) && !IDENTIFIER.test(node.text) ? [node] : [];
	}
	if (ts.isTemplateExpression(node)) {
		return [node.head, ...node.templateSpans.map((span) => span.literal)]
			.filter((part) => LETTER.test(part.text))
			.concat(node.templateSpans.flatMap((span) => shownStrings(span.expression)));
	}
	if (ts.isParenthesizedExpression(node) || ts.isAsExpression(node) || ts.isNonNullExpression(node) ||
		ts.isSatisfiesExpression(node)) {
		return shownStrings(node.expression);
	}
	if (ts.isConditionalExpression(node)) return [...shownStrings(node.whenTrue), ...shownStrings(node.whenFalse)];
	if (ts.isBinaryExpression(node)) {
		return SHOWN_OPERATORS.has(node.operatorToken.kind) ? [...shownStrings(node.left), ...shownStrings(node.right)] : [];
	}
	if (ts.isArrayLiteralExpression(node)) return node.elements.flatMap(shownStrings);
	if (ts.isCallExpression(node)) {
		if (ts.isIdentifier(node.expression) && node.expression.text === '$t') return [];
		return ts.isPropertyAccessExpression(node.expression) ? shownStrings(node.expression.expression) : [];
	}
	return [];
}

/** The name of an identifier, a string literal or a property access; undefined for anything else. */
function nameOf(node) {
	if (!node) return undefined;
	if (ts.isIdentifier(node) || ts.isStringLiteral(node)) return node.text;
	if (ts.isPropertyAccessExpression(node)) return node.name.text;
	return undefined;
}

/** The untranslated texts of a <script> (see 2. above), with offsets in the whole file. */
function scriptTexts({ body, offset }, file) {
	const found = [];
	const visit = (node) => {
		let value;
		if (ts.isVariableDeclaration(node) && TEXT_VARIABLE.test(nameOf(node.name) ?? '')) {
			value = node.initializer;
		} else if (ts.isPropertyAssignment(node) && TEXT_PROPERTY.test(nameOf(node.name) ?? '')) {
			value = node.initializer;
		} else if (
			ts.isBinaryExpression(node) &&
			[ts.SyntaxKind.EqualsToken, ts.SyntaxKind.BarBarEqualsToken, ts.SyntaxKind.QuestionQuestionEqualsToken]
				.includes(node.operatorToken.kind) &&
			TEXT_VARIABLE.test(nameOf(node.left) ?? '')
		) {
			value = node.right;
		} else if (ts.isCallExpression(node) && DIALOGS.has(nameOf(node.expression) ?? '')) {
			value = node.arguments[0];
		}
		for (const literal of shownStrings(value)) {
			found.push({ start: offset + literal.getStart(file), text: literal.text });
		}
		ts.forEachChild(node, visit);
	};
	visit(file);
	return found;
}

/** Whether the object `node` (estree or TypeScript) has a property n: true, false, or null when unknown. */
function hasCount(properties, nameOf) {
	let unknown = false;
	for (const property of properties) {
		const name = nameOf(property);
		if (name === 'n') return true;
		if (name === undefined) unknown = true;
	}
	return unknown ? null : false;
}

/** The $t calls of the markup: [{ start, key (null: not a literal), count (see hasCount) }]. */
function markupCalls(html) {
	const calls = [];
	walk(html, {
		enter(node) {
			if (node.type !== 'CallExpression' || !isT(node.callee)) return;
			const [first, second] = node.arguments;
			const key =
				first?.type === 'Literal' && typeof first.value === 'string'
					? first.value
					: first?.type === 'TemplateLiteral' && first.expressions.length === 0
						? first.quasis[0].value.cooked
						: null;
			const count =
				second === undefined
					? false
					: second.type === 'ObjectExpression'
						? hasCount(second.properties, (property) =>
								property.type === 'Property' && !property.computed
									? (property.key.name ?? property.key.value)
									: undefined
							)
						: null;
			calls.push({ start: node.start, key, count });
		}
	});
	return calls;
}

/** The $t calls of a <script>, with offsets in the whole file. */
function scriptCalls({ offset }, file) {
	const calls = [];
	const visit = (node) => {
		if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === '$t') {
			const [first, second] = node.arguments;
			const key = first && (ts.isStringLiteral(first) || ts.isNoSubstitutionTemplateLiteral(first)) ? first.text : null;
			const count =
				second === undefined
					? false
					: ts.isObjectLiteralExpression(second)
						? hasCount(second.properties, (property) =>
								property.name && (ts.isIdentifier(property.name) || ts.isStringLiteral(property.name))
									? property.name.text
									: undefined
							)
						: null;
			calls.push({ start: offset + node.getStart(file), key, count });
		}
		ts.forEachChild(node, visit);
	};
	visit(file);
	return calls;
}

// --- 4. The list of files not translated yet -----------------------------------------------------

/** The entries of scripts/i18n-untranslated.txt: file (relative to webui/) to its line. */
function readList() {
	const entries = new Map();
	if (!existsSync(LIST)) return entries;
	readFileSync(LIST, 'utf8')
		.split('\n')
		.forEach((raw, index) => {
			const entry = raw.trim();
			if (!entry || entry.startsWith('#')) return;
			if (entries.has(entry)) errors.push(`${relative(LIST)}:${index + 1}: ${entry} is listed twice`);
			entries.set(entry, index + 1);
		});
	return entries;
}

// --- The check ---------------------------------------------------------------------------------

const english = await checkCatalogs();
const listed = readList();
const files = svelteFiles();
let pending = 0;

for (const file of files) {
	const name = relative(file);
	const source = readFileSync(file, 'utf8');
	const { markup, scripts } = split(source);
	let ast;
	try {
		ast = parse(markup, { filename: name });
	} catch (error) {
		errors.push(`${name}:${error.start ? `${error.start.line}:${error.start.column + 1}` : '1:1'}: ` +
			`cannot be parsed: ${error.message}`);
		continue;
	}

	const parsed = scripts.map((script) => ({
		script,
		file: ts.createSourceFile('script.ts', script.body, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
	}));
	const texts = [...markupTexts(ast.html), ...parsed.flatMap(({ script, file }) => scriptTexts(script, file))]
		.sort((a, b) => a.start - b.start);
	if (listed.has(name)) {
		if (texts.length === 0) {
			errors.push(`${relative(LIST)}:${listed.get(name)}: ${name} has no untranslated text: ` +
				'take it off the list');
		}
		pending += 1;
	} else {
		for (const { start, text } of texts) {
			errors.push(`${name}:${position(source, start)}: untranslated text ${quote(text)}`);
		}
	}

	const calls = [...markupCalls(ast.html), ...parsed.flatMap(({ script, file }) => scriptCalls(script, file))];
	for (const { start, key, count } of calls) {
		if (key === null) continue;
		const at = `${name}:${position(source, start)}`;
		if (!english.has(key)) {
			errors.push(`${at}: $t('${key}'): no such key in the catalogs`);
		} else if (isObject(english.get(key).message) && count === false) {
			errors.push(`${at}: $t('${key}'): a text with plural forms needs its count, $t('${key}', { n })`);
		}
	}
}

const known = new Set(files.map(relative));
for (const [entry, line] of listed) {
	if (!known.has(entry)) errors.push(`${relative(LIST)}:${line}: ${entry} is not a .svelte file under src/`);
}

if (errors.length > 0) {
	for (const error of errors) console.error(error);
	console.error(`i18n: ${errors.length} problem(s). Texts come from the catalogs as $t('area.key') ` +
		'(webui/AGENTS.md, ADR-0273).');
	process.exit(1);
}
console.log(`i18n: ${english.size} keys in en and ru; ${files.length} .svelte files, ${pending} of them ` +
	`not translated yet (${relative(LIST)})`);
