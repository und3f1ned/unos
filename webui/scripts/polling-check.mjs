#!/usr/bin/env node
// Pages get changing data over the WebSocket, never by polling the API (ADR-0006, ADR-0363).
// supervisor/tests/test_webui_polling.py runs this check on the web UI (make test) and tests it.
//
//   node scripts/polling-check.mjs [webui directory]      (default: the webui/ of this script)
//
// It fails for every setInterval of src/ (the <script> blocks of the .svelte files and the .ts
// modules) whose callback reaches the API:
// - The API is fetch() and every function or object that a file imports from src/lib/api/
//   ($lib/api/..., or a relative path that leads there): getLogEntries, api.get.
// - The callback reaches what its body names, and the bodies of the functions of its file that it
//   names, again and again: a function declared with `function`, a variable or a `$:` statement
//   whose value is a function. A function literal inside a body counts as run (a .map() or a
//   .forEach() runs it at once, and the check cannot tell one that runs later).
// - A callback that is neither a function literal nor the name of such a function fails too:
//   the check cannot see what it reaches.
// A comment `polling-ignore: <reason>` above the statement of the setInterval exempts it: a timer
// that counts down and asks the server once when its time has run out is no polling. Review reads
// the reasons; the summary counts the exempted timers. A loop of setTimeout calls is out of the
// check's reach: review looks for one.

import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const WEBUI = path.resolve(process.argv[2] ?? path.join(path.dirname(fileURLToPath(import.meta.url)), '..'));
const SRC = path.join(WEBUI, 'src');
const API_DIR = path.join(SRC, 'lib', 'api');
const EXEMPTION = /polling-ignore:\s*\S/;
const relative = (file) => path.relative(WEBUI, file).split(path.sep).join('/');

/** The <script> blocks of a .svelte file, or the whole .ts module, each with its offset. */
function scripts(file, source) {
	if (file.endsWith('.ts')) return [{ body: source, offset: 0 }];
	const found = [];
	for (const match of source.matchAll(/(<script\b[^>]*>)([\s\S]*?)<\/script>/g)) {
		found.push({ body: match[2], offset: match.index + match[1].length });
	}
	return found;
}

/** Whether the module `specifier`, imported by `file`, is under src/lib/api/. */
function isApiModule(specifier, file) {
	if (specifier === '$lib/api' || specifier.startsWith('$lib/api/')) return true;
	if (!specifier.startsWith('.')) return false;
	const target = path.resolve(path.dirname(file), specifier);
	return target === API_DIR || target.startsWith(API_DIR + path.sep);
}

/** The names a script imports from the API, each with its module. */
function apiImports(sourceFile, file) {
	const names = new Map();
	for (const statement of sourceFile.statements) {
		if (!ts.isImportDeclaration(statement) || !statement.importClause) continue;
		const clause = statement.importClause;
		const specifier = statement.moduleSpecifier.text;
		if (clause.isTypeOnly || !isApiModule(specifier, file)) continue;
		if (clause.name) names.set(clause.name.text, specifier);
		const bindings = clause.namedBindings;
		if (bindings && ts.isNamespaceImport(bindings)) names.set(bindings.name.text, specifier);
		if (bindings && ts.isNamedImports(bindings)) {
			for (const element of bindings.elements) {
				if (!element.isTypeOnly) names.set(element.name.text, specifier);
			}
		}
	}
	return names;
}

const isFunction = (node) => node && (ts.isArrowFunction(node) || ts.isFunctionExpression(node));

/** The functions a script declares, by name: their bodies are followed. */
function localFunctions(sourceFile) {
	const functions = new Map();
	const visit = (node) => {
		if (ts.isFunctionDeclaration(node) && node.name && node.body) functions.set(node.name.text, node);
		if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && isFunction(node.initializer)) {
			functions.set(node.name.text, node.initializer);
		}
		// $: name = (...) => ...
		if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.EqualsToken &&
			ts.isIdentifier(node.left) && isFunction(node.right)) {
			functions.set(node.left.text, node.right);
		}
		ts.forEachChild(node, visit);
	};
	visit(sourceFile);
	return functions;
}

/** Whether `node`, an identifier, names something rather than being a property's or a declaration's name. */
function isReference(node) {
	const parent = node.parent;
	if (ts.isPropertyAccessExpression(parent) && parent.name === node) return false;
	if ((ts.isPropertyAssignment(parent) || ts.isMethodDeclaration(parent) || ts.isPropertyDeclaration(parent) ||
		ts.isBindingElement(parent) || ts.isParameter(parent) || ts.isVariableDeclaration(parent) ||
		ts.isFunctionDeclaration(parent) || ts.isImportSpecifier(parent)) && parent.name === node) return false;
	return !ts.isTypeReferenceNode(parent) && !ts.isQualifiedName(parent);
}

/** Whether `node` is a call of setInterval (also window.setInterval, globalThis.setInterval). */
function isSetInterval(node) {
	if (!ts.isCallExpression(node)) return false;
	const callee = node.expression;
	if (ts.isIdentifier(callee)) return callee.text === 'setInterval';
	return ts.isPropertyAccessExpression(callee) && callee.name.text === 'setInterval' &&
		ts.isIdentifier(callee.expression) && ['window', 'globalThis', 'self'].includes(callee.expression.text);
}

/** Whether a comment `polling-ignore: <reason>` above the statement of `node` exempts it. */
function exempted(node, sourceFile) {
	const text = sourceFile.getFullText();
	let statement = node;
	while (statement.parent && !ts.isSourceFile(statement.parent) && !ts.isBlock(statement.parent) &&
		!ts.isCaseClause(statement.parent) && !ts.isDefaultClause(statement.parent)) {
		statement = statement.parent;
	}
	const comments = ts.getLeadingCommentRanges(text, statement.pos) ?? [];
	return comments.some((range) => EXEMPTION.test(text.slice(range.pos, range.end)));
}

/**
 * What the callback of a setInterval reaches of the API: the first use found, with the functions
 * of the file it goes through; null when it reaches none, or a reason when the check cannot tell.
 */
function reach(callback, imports, functions) {
	let start = callback;
	const via = [];
	if (ts.isIdentifier(callback)) {
		if (imports.has(callback.text)) return { name: callback.text, module: imports.get(callback.text), via };
		start = functions.get(callback.text);
		if (!start) return { unknown: callback.text };
		via.push(callback.text);
	} else if (!isFunction(callback)) {
		return { unknown: callback.getText() };
	}
	const seen = new Set([start]);
	const queue = [{ node: start, via }];
	while (queue.length > 0) {
		const { node, via } = queue.shift();
		let found = null;
		const visit = (child) => {
			if (found) return;
			if (ts.isIdentifier(child) && isReference(child)) {
				const name = child.text;
				if (imports.has(name)) found = { name, module: imports.get(name), via };
				else if (name === 'fetch') found = { name, module: 'the browser', via };
				else if (functions.has(name) && !seen.has(functions.get(name))) {
					seen.add(functions.get(name));
					queue.push({ node: functions.get(name), via: [...via, name] });
				}
			}
			ts.forEachChild(child, visit);
		};
		ts.forEachChild(node, visit);
		if (found) return found;
	}
	return null;
}

function sourceFiles() {
	return readdirSync(SRC, { recursive: true })
		.filter((name) => name.endsWith('.svelte') || (name.endsWith('.ts') && !name.endsWith('.d.ts')))
		.map((name) => path.join(SRC, name))
		.sort();
}

const problems = [];
let timers = 0;
let exempt = 0;
const files = sourceFiles();
for (const file of files) {
	const source = readFileSync(file, 'utf8');
	for (const script of scripts(file, source)) {
		const sourceFile = ts.createSourceFile('script.ts', script.body, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
		const imports = apiImports(sourceFile, file);
		const functions = localFunctions(sourceFile);
		const visit = (node) => {
			if (isSetInterval(node)) {
				timers += 1;
				const line = source.slice(0, script.offset + node.getStart(sourceFile)).split('\n').length;
				const at = `${relative(file)}:${line}`;
				if (exempted(node, sourceFile)) {
					exempt += 1;
				} else if (node.arguments.length === 0) {
					problems.push(`${at}: setInterval without a callback`);
				} else {
					const found = reach(node.arguments[0], imports, functions);
					if (found?.unknown) {
						problems.push(`${at}: setInterval calls ${found.unknown}, which the check cannot see into: ` +
							'give it a function literal or a function of this file');
					} else if (found) {
						const through = found.via.length > 0 ? ` through ${found.via.join(' -> ')}` : '';
						problems.push(`${at}: setInterval reaches the API: ${found.name} (from ${found.module})${through}`);
					}
				}
			}
			ts.forEachChild(node, visit);
		};
		visit(sourceFile);
	}
}

if (problems.length > 0) {
	for (const problem of problems) console.error(problem);
	console.error(`polling: ${problems.length} problem(s). Changing data comes over the WebSocket, never by ` +
		'polling the API (ADR-0006, ADR-0363).');
	process.exit(1);
}
console.log(`polling: ${files.length} files, ${timers} setInterval call(s), none reaches the API ` +
	`(${exempt} exempted with polling-ignore)`);
