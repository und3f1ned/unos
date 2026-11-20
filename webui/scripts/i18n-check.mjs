#!/usr/bin/env node
// The texts of the web UI come from catalogs with English and Russian (ADR-0287). make frontend
// runs this check (npm run i18n); supervisor/tests/test_i18n.py tests it.
//
//   node scripts/i18n-check.mjs [webui directory]      (default: the webui/ of this script)
//
// 1. The catalogs, src/lib/i18n/catalog/<area>.ts: each exports `en` and `ru` with the same keys,
//    every key starts with "<area>.", no text is empty, a text with plural forms has the forms
//    that whole numbers take in its language (Intl.PluralRules: one and other in English; one,
//    few and many in Russian), and both languages have the same {placeholders}.
// 2. No .svelte file under src/ has a text with letters that does not come from $t(...):
//    - no text between tags, and none in a user-facing attribute or prop (placeholder, title,
//      alt, aria-label, label, hint, description, error, a prop named ...Label, ...Hint, ...Text
//      and the like) or in any prop of a component whose value does not look like code;
//    - no string literal in the <script> or in an expression of the markup (blocks, {@const},
//      {@render}, the defaults of a {#snippet}'s parameters, directives, props), wherever it
//      goes: to a variable, a property, an argument, a return value, a thrown error or a
//      placeholder of $t. A literal is not a text where it is compared
//      or looked up (x === 'Escape', ua.includes('Chrome'), case 'tcp':), where it names a module,
//      a type, a key of an object, an argument of console.* or the key of $t, where it goes to a
//      class or a style, and where it looks like code: a name (primary, self_signed), an id or a
//      class (task-time-zone-hint, bg-red-500/20 text-red-400), a path, a URL, a MIME type, a
//      locale (en-GB) or a CSS length. Where a text goes (a variable, a property or a prop named
//      like a text, confirm(), alert() and prompt(), a placeholder of $t) or where the markup
//      shows it ({busy ? 'Saving...' : x}), even a name is a text.
//    - A comment `i18n-ignore: <reason>` above a statement or a property of an object (or at the
//      end of its line) exempts its literals: a command or a name that is the same in every
//      language. Review reads the reasons; the summary counts the exempted literals.
//    A text that the code puts together from data at run time, a single lower-case word in a
//    script (taken for a name), and the texts of .ts modules are out of the check's reach: review
//    and a walk through the pages in Russian check them.
// 3. Every key that a $t('...') call names exists in English, and a text with plural forms gets
//    its count: $t('area.key', { n }). A literal that starts with the name of a catalog and a dot
//    is a key too, as in a map from values to keys ({ allow: 'account.firewall.allow' }): it
//    exists. The same holds in the .ts modules of src/ (but $lib/i18n), whose helpers take $t
//    (`t('area.key')`); their other texts are for review.
// 4. scripts/i18n-untranslated.txt lists the .svelte files not translated yet. Their texts are
//    not reported, and a listed file without such texts fails until it leaves the list.

import { existsSync, readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse } from 'svelte/compiler';
import { walk } from './svelte-walk.mjs';
import ts from 'typescript';

const LOCALES = ['en', 'ru'];
const LETTER = /\p{L}/u;
const PLACEHOLDER = /\{(\w+)\}/g;
// Attributes and props whose value people read (or hear, from a screen reader).
const TEXT_ATTRIBUTE =
	/^(?:placeholder|title|alt|label|hint|description|error|message|text|tooltip|aria-(?:label|description|roledescription|valuetext|placeholder))$|(?:Label|Hint|Title|Text|Message|Description|Placeholder|Tooltip)$/;
// Attributes whose value is styling, never a text.
const STYLE_ATTRIBUTE = /^(?:class|style)$/;
// A variable of a script (or a property set by assignment) whose name says that it holds a text.
const TEXT_VARIABLE =
	/^(?:label|title|text|message|description|hint|placeholder|tooltip|caption|heading|error|notice|warning|success)$|[a-z](?:Label|Title|Text|Message|Description|Hint|Placeholder|Tooltip|Caption|Heading|Error|Notice|Warning|Success)$|^(?:[A-Z0-9]+_)*(?:LABEL|TITLE|TEXT|MESSAGE|DESCRIPTION|HINT|NOTE|WARNING|ERROR|NOTICE|CAPTION|HEADING)$/;
// The same for a property of an object literal, without the words that also name a variant
// ({ error: 'alert-error', success: 'badge-success' }).
const TEXT_PROPERTY =
	/^(?:label|title|text|message|description|hint|placeholder|tooltip|caption|heading)$|[a-z](?:Label|Title|Text|Message|Description|Hint|Placeholder|Tooltip|Caption|Heading)$/;
// A variable or a property that holds classes or styles (variantClasses, sizeClass, style).
const STYLE_NAME = /(?:^|[a-z_])(?:class|classes|className|style|styles)$/i;
// The browser's dialogs show their first argument.
const DIALOGS = new Set(['confirm', 'alert', 'prompt']);
// Calls whose first argument is looked up, compared or named, not shown: ua.includes('Chrome'),
// line.startsWith('Status:'), dispatch('close'), localStorage.getItem(KEY). Of LOOKED_IN, the
// receiver too: ['Enter', 'Space'].includes(key).
const LOOKED_IN = new Set(['includes', 'indexOf', 'lastIndexOf', 'has']);
const LOOKUPS = new Set([...LOOKED_IN, 'startsWith', 'endsWith', 'split', 'replace', 'replaceAll', 'match',
	'matchAll', 'search', 'get', 'getAll', 'getItem', 'setItem', 'removeItem', 'querySelector',
	'querySelectorAll', 'getElementById', 'closest', 'matches', 'addEventListener', 'removeEventListener',
	'getAttribute', 'setAttribute', 'removeAttribute', 'hasAttribute', 'toggleAttribute', 'createElement',
	'dispatch']);
// An element id or a class (task-time-zone-hint): not a text, even where a text goes.
const IDENTIFIER = /^[a-z0-9]+(?:-[a-z0-9]+)+$/;
// A literal of code: a name (primary, self_signed, keydown, smbEnabled, _blank); an id, a class, a
// file or a MIME type (task-time-zone-hint, hover:bg-blue-700, text/plain); a list of options
// (rw,sync,no_subtree_check); a URL, a path, a fragment or a query; a locale (en-GB); a CSS length
// or a duration; an HTTP method.
const CODE = [
	/^_?[a-z][a-zA-Z0-9_]*$/,
	/^[a-z0-9]+(?:[-_.:/][a-z0-9]+)+$/,
	/^[a-z0-9_=]+(?:,[a-z0-9_=]+)+$/,
	/^(?:[a-z][a-z0-9+.-]*:\/\/|\/|\.\.?\/|#|\?)\S*$/i,
	/^[a-z]{2,3}(?:-[A-Z][a-z]{3})?(?:-[A-Z]{2}|-\d{3})?$/,
	/^-?\d*\.?\d+(?:px|rem|em|ch|vh|vw|ms|s|deg|fr)$/,
	/^(?:GET|HEAD|POST|PUT|PATCH|DELETE|OPTIONS)$/
];
// Classes, as in 'btn btn-primary' or 'flex items-center gap-2': every word a class with a
// separator, or a one-word class of Tailwind or of src/app.css.
const CLASS = /^!?-?[a-z0-9]+(?:[-:/.[\]%#()_]+[a-z0-9%#\])]*)+$/;
const ONE_WORD_CLASSES = new Set(['flex', 'grid', 'block', 'inline', 'hidden', 'contents', 'table', 'relative',
	'absolute', 'fixed', 'sticky', 'static', 'truncate', 'uppercase', 'lowercase', 'capitalize', 'italic',
	'underline', 'border', 'rounded', 'shadow', 'transition', 'invisible', 'visible', 'grow', 'shrink', 'container',
	'group', 'peer', 'outline', 'ring', 'isolate', 'antialiased', 'alert', 'badge', 'btn', 'card', 'modal', 'mono']);
// The exemption of a statement's or a property's literals, with its reason.
const EXEMPTION = /i18n-ignore:\s*\S/;
const COMPARISONS = new Set([ts.SyntaxKind.EqualsEqualsEqualsToken, ts.SyntaxKind.ExclamationEqualsEqualsToken,
	ts.SyntaxKind.EqualsEqualsToken, ts.SyntaxKind.ExclamationEqualsToken]);
const ASSIGNMENTS = new Set([ts.SyntaxKind.EqualsToken, ts.SyntaxKind.BarBarEqualsToken,
	ts.SyntaxKind.QuestionQuestionEqualsToken, ts.SyntaxKind.PlusEqualsToken]);
// Operators whose result is one of their operands, or both joined: what the result shows.
const SHOWN_OPERATORS = new Set([ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken,
	ts.SyntaxKind.PlusToken]);

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

/** Check every catalog; returns the areas and the English texts of all of them, by key. */
async function checkCatalogs() {
	const english = new Map();
	const files = existsSync(CATALOG_DIR)
		? readdirSync(CATALOG_DIR).filter((name) => name.endsWith('.ts')).sort()
		: [];
	if (files.length === 0) errors.push(`${relative(CATALOG_DIR)}: no catalogs`);
	const areas = new Set(files.map((name) => name.slice(0, -'.ts'.length)));
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
	return { areas, english };
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
 * scripts and the expressions of the markup.
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

/** Whether `text` (a literal, or a template with x for each placeholder) looks like code. */
function looksLikeCode(text) {
	if (CODE.some((pattern) => pattern.test(text))) return true;
	const words = text.trim().split(/\s+/);
	return text.trim() !== '' && words.every((word) => CLASS.test(word) || ONE_WORD_CLASSES.has(word));
}

/** The name of an identifier, a string literal or a property access; undefined for anything else. */
function nameOf(node) {
	if (!node) return undefined;
	if (ts.isIdentifier(node) || ts.isStringLiteral(node) || ts.isPrivateIdentifier(node)) return node.text;
	if (ts.isPropertyAccessExpression(node)) return node.name.text;
	return undefined;
}

/** Whether a comment `i18n-ignore: <reason>` above the statement or property of `node` exempts it. */
function exempted(node, file) {
	const text = file.getFullText();
	for (let current = node; current.parent && !ts.isSourceFile(current); current = current.parent) {
		const { parent } = current;
		const member = ts.isSourceFile(parent) || ts.isBlock(parent) || ts.isCaseClause(parent) ||
			ts.isDefaultClause(parent) || ts.isLabeledStatement(parent) || ts.isObjectLiteralExpression(parent);
		if (!member) continue;
		const comments = [...(ts.getLeadingCommentRanges(text, current.pos) ?? []),
			...(ts.getTrailingCommentRanges(text, current.end) ?? [])];
		if (comments.some((range) => EXEMPTION.test(text.slice(range.pos, range.end)))) return true;
	}
	return false;
}

/**
 * Reads the literals of `root`, a node of `file` (TypeScript) whose offset in the .svelte file is
 * `base` plus its own, in `context`:
 * - 'shown': shown as it is (a text unless it is an id),
 * - 'code': a text unless it looks like code (or names a key of a catalog),
 * - 'skip': not a text (compared, looked up, a key, a type).
 * Pushes the texts to found ({ start, text }) and the keys of a catalog to keys ({ start, key }).
 */
function readLiterals(root, context, file, base, { areas, found, keys, exemptions }) {
	const at = (node) => base + node.getStart(file);
	const lower = (context, to) => (context === 'skip' ? 'skip' : to);
	// Where a value given to `name` (a variable, a property set by assignment, a parameter) goes.
	const target = (name, context) =>
		context === 'skip' || STYLE_NAME.test(name ?? '') ? 'skip' : TEXT_VARIABLE.test(name ?? '') ? 'shown' : 'code';

	const literal = (node, parts, shape, context) => {
		if (context === 'skip' || !parts.some((part) => LETTER.test(part.text))) return;
		const area = parts.length === 1 ? /^([a-z][a-zA-Z0-9]*)\.[\w.]*\w$/.exec(shape)?.[1] : undefined;
		if (context === 'code' && areas.has(area)) {
			keys.push({ start: at(node), key: shape });
			return;
		}
		if (context === 'shown' ? IDENTIFIER.test(shape) : looksLikeCode(shape)) return;
		if (exempted(node, file)) {
			exemptions.count += 1;
			return;
		}
		for (const part of parts) {
			if (LETTER.test(part.text)) found.push({ start: at(part), text: part.text });
		}
	};

	const visit = (node, context) => {
		if (!node) return;
		if (ts.isTypeNode(node) || ts.isImportDeclaration(node) || ts.isExportDeclaration(node) ||
			ts.isImportEqualsDeclaration(node) || ts.isTypeAliasDeclaration(node) || ts.isInterfaceDeclaration(node)) {
			return;
		}
		if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
			literal(node, [node], node.text, context);
			return;
		}
		if (ts.isTemplateExpression(node)) {
			const parts = [node.head, ...node.templateSpans.map((span) => span.literal)];
			literal(node, parts, parts.map((part) => part.text).join('x'), context);
			for (const span of node.templateSpans) visit(span.expression, context);
			return;
		}
		if (ts.isParenthesizedExpression(node) || ts.isAsExpression(node) || ts.isNonNullExpression(node) ||
			ts.isSatisfiesExpression(node) || ts.isTypeAssertionExpression(node)) {
			visit(node.expression, context);
			return;
		}
		if (ts.isConditionalExpression(node)) {
			visit(node.condition, lower(context, 'code'));
			visit(node.whenTrue, context);
			visit(node.whenFalse, context);
			return;
		}
		if (ts.isBinaryExpression(node)) {
			const operator = node.operatorToken.kind;
			if (COMPARISONS.has(operator)) {
				visit(node.left, 'skip');
				visit(node.right, 'skip');
			} else if (operator === ts.SyntaxKind.InKeyword) {
				visit(node.left, 'skip');
				visit(node.right, lower(context, 'code'));
			} else if (ASSIGNMENTS.has(operator)) {
				visit(node.left, lower(context, 'code'));
				visit(node.right, target(nameOf(node.left), context));
			} else if (operator === ts.SyntaxKind.AmpersandAmpersandToken) {
				visit(node.left, lower(context, 'code'));
				visit(node.right, context);
			} else if (SHOWN_OPERATORS.has(operator)) {
				visit(node.left, context);
				visit(node.right, context);
			} else {
				visit(node.left, lower(context, 'code'));
				visit(node.right, lower(context, 'code'));
			}
			return;
		}
		if (ts.isVariableDeclaration(node) || ts.isParameter(node) || ts.isPropertyDeclaration(node) ||
			ts.isBindingElement(node)) {
			// A destructuring pattern has defaults of its own: const { label = 'None' } = row.
			if (ts.isObjectBindingPattern(node.name) || ts.isArrayBindingPattern(node.name)) {
				visit(node.name, lower(context, 'code'));
			}
			visit(node.initializer, target(nameOf(node.name), context));
			return;
		}
		if (ts.isArrayLiteralExpression(node)) {
			for (const element of node.elements) visit(element, context);
			return;
		}
		if (ts.isObjectLiteralExpression(node)) {
			for (const property of node.properties) {
				if (ts.isPropertyAssignment(property)) {
					if (ts.isComputedPropertyName(property.name)) visit(property.name.expression, 'skip');
					// Even in an object that is shown, a property not named like a text may hold
					// code: notice = { variant: 'info', text }.
					const name = nameOf(property.name);
					visit(property.initializer, context === 'skip' || STYLE_NAME.test(name ?? '') ? 'skip'
						: TEXT_PROPERTY.test(name ?? '') ? 'shown' : 'code');
				} else if (ts.isShorthandPropertyAssignment(property)) {
					visit(property.objectAssignmentInitializer, lower(context, 'code'));
				} else {
					visit(property, lower(context, 'code'));
				}
			}
			return;
		}
		if (ts.isCallExpression(node)) {
			const callee = node.expression;
			const name = nameOf(callee);
			const [first, ...rest] = node.arguments;
			if (callee.kind === ts.SyntaxKind.ImportKeyword) return;
			if (ts.isPropertyAccessExpression(callee) && nameOf(callee.expression) === 'console') return;
			if (ts.isIdentifier(callee) && name === '$t') {
				// A literal key (3. checks it), or an expression that picks one: $t(LABELS[value]).
				if (first && !ts.isStringLiteral(first) && !ts.isNoSubstitutionTemplateLiteral(first)) {
					visit(first, lower(context, 'code'));
				}
				// The values of the placeholders are shown, whatever their names.
				for (const argument of rest) {
					if (!ts.isObjectLiteralExpression(argument)) {
						visit(argument, lower(context, 'code'));
						continue;
					}
					for (const property of argument.properties) {
						visit(ts.isPropertyAssignment(property) ? property.initializer : property,
							lower(context, ts.isPropertyAssignment(property) ? 'shown' : 'code'));
					}
				}
				return;
			}
			if (DIALOGS.has(name) && (ts.isIdentifier(callee) || nameOf(callee.expression) === 'window')) {
				visit(first, lower(context, 'shown'));
				for (const argument of rest) visit(argument, lower(context, 'code'));
				return;
			}
			if (LOOKUPS.has(name)) {
				if (ts.isPropertyAccessExpression(callee)) {
					visit(callee.expression, LOOKED_IN.has(name) ? 'skip' : lower(context, 'code'));
				}
				visit(first, 'skip');
				for (const argument of rest) visit(argument, lower(context, 'code'));
				return;
			}
			// A method of a text shows what it is called on: ('None' || x).toUpperCase().
			visit(ts.isPropertyAccessExpression(callee) ? callee.expression : callee,
				ts.isPropertyAccessExpression(callee) ? context : lower(context, 'code'));
			for (const argument of node.arguments) visit(argument, lower(context, 'code'));
			return;
		}
		if (ts.isNewExpression(node)) {
			visit(node.expression, lower(context, 'code'));
			// A set is looked up: new Set(['ArrowDown', 'ArrowUp']).
			const set = nameOf(node.expression) === 'Set';
			for (const argument of node.arguments ?? []) visit(argument, set ? 'skip' : lower(context, 'code'));
			return;
		}
		if (ts.isElementAccessExpression(node)) {
			visit(node.expression, context);
			visit(node.argumentExpression, 'skip');
			return;
		}
		if (ts.isPropertyAccessExpression(node)) {
			visit(node.expression, lower(context, 'code'));
			return;
		}
		if (ts.isCaseClause(node)) {
			visit(node.expression, 'skip');
			for (const statement of node.statements) visit(statement, lower(context, 'code'));
			return;
		}
		if (ts.isPropertyAssignment(node) || ts.isMethodDeclaration(node) || ts.isGetAccessor(node) ||
			ts.isSetAccessor(node) || ts.isEnumMember(node)) {
			if (node.name && ts.isComputedPropertyName(node.name)) visit(node.name.expression, 'skip');
			ts.forEachChild(node, (child) => child !== node.name && visit(child, lower(context, 'code')));
			return;
		}
		ts.forEachChild(node, (child) => visit(child, lower(context, 'code')));
	};

	visit(root, context);
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

/** The texts and the keys of the markup (2. and 3.), read into `into`. */
function readMarkup(html, markup, into) {
	const expression = (node, context) => {
		if (!node || typeof node !== 'object' || typeof node.start !== 'number') return;
		const file = ts.createSourceFile('expression.ts', `(${markup.slice(node.start, node.end)})`,
			ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
		const [statement] = file.statements;
		const root = statement && ts.isExpressionStatement(statement) ? statement.expression : file;
		readLiterals(root, root === file ? 'code' : context, file, node.start - 1, into);
	};
	// The parameters of a {#snippet}, read as those of an arrow function: their defaults.
	const parameters = (list) => {
		if (!Array.isArray(list) || list.length === 0) return;
		const start = list[0].start;
		const file = ts.createSourceFile('parameters.ts', `((${markup.slice(start, list.at(-1).end)}) => 0)`,
			ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
		readLiterals(file, 'code', file, start - 2, into);
	};
	walk(html, {
		enter(node, parent) {
			switch (node.type) {
				case 'Comment':
				case 'StyleDirective':
					this.skip();
					break;
				case 'Text':
					into.found.push(...textNode(node));
					break;
				case 'MustacheTag':
				case 'RawMustacheTag':
					expression(node.expression, 'shown');
					this.skip();
					break;
				case 'Attribute': {
					if (!STYLE_ATTRIBUTE.test(node.name) && Array.isArray(node.value)) {
						const text = isTextAttribute(node, parent);
						for (const part of node.value) {
							// Any prop of a component can take a text: confirm="Delete".
							if (part.type === 'Text' && (text || (parent?.type === 'InlineComponent' &&
								LETTER.test(part.data) && !looksLikeCode(part.data.trim())))) {
								into.found.push(...textNode(part));
							}
							if (part.type === 'MustacheTag') expression(part.expression, text ? 'shown' : 'code');
						}
					}
					this.skip();
					break;
				}
				case 'Spread':
				case 'Binding':
				case 'Action':
				case 'Transition':
				case 'Animation':
				case 'Class':
				case 'ConstTag':
					expression(node.expression, 'code');
					this.skip();
					break;
				case 'RenderTag':
				case 'AttachTag':
					expression(node.expression, 'code');
					this.skip();
					break;
				case 'SnippetBlock':
					parameters(node.parameters);
					break;
				case 'IfBlock':
				case 'AwaitBlock':
				case 'KeyBlock':
				case 'InlineComponent':
					expression(node.expression, 'code');
					break;
				case 'EachBlock':
					expression(node.expression, 'code');
					expression(node.context, 'code');
					expression(node.key, 'code');
					break;
				case 'Element':
					expression(node.tag, 'code');
					break;
			}
		}
	});
}

const isT = (callee) => callee?.type === 'Identifier' && callee.name === '$t';

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

/** The .ts modules of src/, but the catalogs and $lib/i18n, which define the keys. */
function moduleFiles() {
	const i18n = path.join(SRC, 'lib', 'i18n') + path.sep;
	return readdirSync(SRC, { recursive: true })
		.filter((name) => name.endsWith('.ts') && !name.endsWith('.d.ts'))
		.map((name) => path.join(SRC, name))
		.filter((file) => !file.startsWith(i18n))
		.sort();
}

/**
 * The keys that a .ts module names: every literal that starts with the name of a catalog and a
 * dot, each with its offset and, when it is the first argument of a call (t('key'), $t('key')),
 * the callee and whether a count goes with it (see hasCount).
 */
function moduleKeys(source, areas) {
	const file = ts.createSourceFile('module.ts', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
	const found = [];
	const visit = (node) => {
		if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
			const area = /^([a-z][a-zA-Z0-9]*)\.[\w.]*\w$/.exec(node.text)?.[1];
			if (areas.has(area)) {
				const parent = node.parent;
				const call = ts.isCallExpression(parent) && parent.arguments[0] === node ? parent : null;
				const callee = call && ts.isIdentifier(call.expression) ? call.expression.text : null;
				const second = call?.arguments[1];
				const count = second === undefined ? false : ts.isObjectLiteralExpression(second)
					? hasCount(second.properties, (property) =>
						property.name && (ts.isIdentifier(property.name) || ts.isStringLiteral(property.name))
							? property.name.text : undefined)
					: null;
				found.push({ start: node.getStart(file), key: node.text, call: callee, count });
			}
		}
		ts.forEachChild(node, visit);
	};
	visit(file);
	return found;
}

// --- The check ---------------------------------------------------------------------------------

const { areas, english } = await checkCatalogs();
const listed = readList();
const files = svelteFiles();
let pending = 0;
let exempt = 0;

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

	const into = { areas, found: [], keys: [], exemptions: { count: 0 } };
	readMarkup(ast.html, markup, into);
	const parsed = scripts.map((script) => ({
		script,
		file: ts.createSourceFile('script.ts', script.body, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
	}));
	for (const { script, file } of parsed) readLiterals(file, 'code', file, script.offset, into);
	const texts = into.found.sort((a, b) => a.start - b.start);
	if (listed.has(name)) {
		if (texts.length === 0) {
			errors.push(`${relative(LIST)}:${listed.get(name)}: ${name} has no untranslated text: ` +
				'take it off the list');
		}
		pending += 1;
	} else {
		exempt += into.exemptions.count;
		for (const { start, text } of texts) {
			errors.push(`${name}:${position(source, start)}: untranslated text ${quote(text)}`);
		}
	}

	for (const { start, key } of into.keys) {
		if (!english.has(key)) errors.push(`${name}:${position(source, start)}: '${key}': no such key in the catalogs`);
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

// The .ts modules of src/: the helpers of an area that take $t name its keys too (ADR-0427).
for (const file of moduleFiles()) {
	const name = relative(file);
	const source = readFileSync(file, 'utf8');
	for (const { start, key, call, count } of moduleKeys(source, areas)) {
		const at = `${name}:${position(source, start)}`;
		if (!english.has(key)) {
			errors.push(`${at}: '${key}': no such key in the catalogs`);
		} else if (call && isObject(english.get(key).message) && count === false) {
			errors.push(`${at}: ${call}('${key}'): a text with plural forms needs its count, ${call}('${key}', { n })`);
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
		'(webui/AGENTS.md, ADR-0287).');
	process.exit(1);
}
console.log(`i18n: ${english.size} keys in en and ru; ${files.length} .svelte files, ${pending} of them ` +
	`not translated yet (${relative(LIST)}); ${exempt} literal(s) exempted with i18n-ignore`);
