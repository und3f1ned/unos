#!/usr/bin/env node
// Pages get changing data over the WebSocket, never by polling the API (ADR-0006, ADR-0365).
// supervisor/tests/test_webui_polling.py runs this check on the web UI (make test) and tests it.
//
//   node scripts/polling-check.mjs [webui directory]      (default: the webui/ of this script)
//
// It fails for every setInterval of src/ (the <script> blocks of the .svelte files and the .ts
// modules) whose callback reaches the API, or changes what may reach it:
// - The API is fetch() and every function or object that a file imports from src/lib/api/
//   ($lib/api/..., or a relative path that leads there): getLogEntries, api.get. A function that a
//   .ts module of src/ exports ($lib/..., or a relative path) is API too when it reaches the API,
//   through the modules that module imports in turn; so is a namespace import of a module that
//   exports one.
// - The callback reaches what its body names, and the bodies of the functions of its file that it
//   names, again and again: a function declared with `function`, or a variable whose value is a
//   function. A function literal inside a body counts as run (a .map() or a .forEach() runs it at
//   once, and the check cannot tell one that runs later).
// - A call `obj.method()` reaches the API when a class of a .ts module of src/ (a page's state in
//   a .svelte.ts module) has a method of that name that reaches it, also through its other
//   methods (`this.load()`): by name, whatever `obj` is, as the check has no types.
// - A callback that assigns a variable of a component's state (`$state`, `$state.raw`, `$derived`,
//   a prop of `$props()`; also `name.field = ...`, `name++`) that an `$effect` or `$effect.pre`
//   (anywhere in the script, also given by name) or a `$derived` reaching the API reads fails:
//   Svelte runs it again (ADR-0425). It reads what its function reads, also a variable that it
//   assigns too, and what the functions of its file that it calls read, again and again, but not
//   inside untrack(...) nor the target of a plain `name = value`. So does a callback that assigns
//   a variable that a `$derived` reads whose value such an effect reads, and so on.
// - A callback that changes a store fails: `name.set(...)`, `name.update(...)` or `$name = ...`,
//   where name is declared with writable, readable or derived in its file or in the .ts module it
//   comes from, or read as `$name` in its file. What reads a store can be in any file.
// - A callback that is neither a function literal nor the name of such a function fails too:
//   the check cannot see what it reaches.
// A comment `polling-ignore: <reason>` above the statement of the setInterval exempts it: a timer
// that counts down and asks the server once when its time has run out is no polling. Review reads
// the reasons; the summary counts the exempted timers. Out of the check's reach, for review: a loop
// of setTimeout calls; the markup of a component ({#await}, {#key} on a variable a timer changes);
// a `$state` field of a class that a timer changes and an effect reads;
// a function of another module that changes a store; objects and stores that a module exports
// with methods that reach the API; and, in a cycle of imports, the module that the cycle comes
// back to, which is taken as reaching nothing while its importers are read.

import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const WEBUI = path.resolve(process.argv[2] ?? path.join(path.dirname(fileURLToPath(import.meta.url)), '..'));
const SRC = path.join(WEBUI, 'src');
const API_DIR = path.join(SRC, 'lib', 'api');
const EXEMPTION = /polling-ignore:\s*\S/;
const STORE_FACTORIES = new Set(['writable', 'readable', 'derived']);
// Svelte's runes: a `$` name that is not a store.
const RUNES = new Set(['$state', '$derived', '$effect', '$props', '$bindable', '$inspect', '$host']);
const SCRIPT = /(<script\b[^>]*>)([\s\S]*?)<\/script>/g;
const relative = (file) => path.relative(WEBUI, file).split(path.sep).join('/');
const parse = (text) => ts.createSourceFile('script.ts', text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
const through = (via) => (via.length > 0 ? ` through ${via.join(' -> ')}` : '');
/** A use of the API: `found.name` (from `found.module`), through the functions `found.via`. */
const describe = (found) => `${found.name} (from ${found.module})${through(found.via)}`;

/** The <script> blocks of a .svelte file, or the whole .ts module, each with its offset. */
function scripts(file, source) {
	if (file.endsWith('.ts')) return [{ body: source, offset: 0 }];
	return [...source.matchAll(SCRIPT)].map((match) => ({ body: match[2], offset: match.index + match[1].length }));
}

/** Whether the module `specifier`, imported by `file`, is under src/lib/api/. */
function isApiModule(specifier, file) {
	if (specifier === '$lib/api' || specifier.startsWith('$lib/api/')) return true;
	if (!specifier.startsWith('.')) return false;
	const target = path.resolve(path.dirname(file), specifier);
	return target === API_DIR || target.startsWith(API_DIR + path.sep);
}

/** The .ts module of src/ that `specifier`, imported by `file`, names; null for any other module. */
function sourceModule(specifier, file) {
	let base;
	if (specifier === '$lib' || specifier.startsWith('$lib/')) base = path.join(SRC, 'lib', specifier.slice(4));
	else if (specifier.startsWith('.')) base = path.resolve(path.dirname(file), specifier);
	else return null;
	const candidates = [base, base.replace(/\.js$/, '.ts'), `${base}.ts`, path.join(base, 'index.ts')];
	return candidates.find((candidate) => candidate.startsWith(SRC + path.sep) && candidate.endsWith('.ts') &&
		!candidate.endsWith('.d.ts') && existsSync(candidate) && statSync(candidate).isFile()) ?? null;
}

const isFunction = (node) => node && (ts.isArrowFunction(node) || ts.isFunctionExpression(node));
const hasModifier = (node, kind) => (ts.canHaveModifiers(node) ? ts.getModifiers(node) ?? [] : []).some((m) => m.kind === kind);

/** The functions a script declares, by name: their bodies are followed. */
function localFunctions(sourceFile) {
	const functions = new Map();
	const visit = (node) => {
		if (ts.isFunctionDeclaration(node) && node.name && node.body) functions.set(node.name.text, node);
		if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && isFunction(node.initializer)) {
			functions.set(node.name.text, node.initializer);
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

/** The variables that an assignment to `target` changes: `a`, `a.b[c]`, `[a, b]`, `{ a, b: c }`. */
function targetNames(target) {
	while (ts.isPropertyAccessExpression(target) || ts.isElementAccessExpression(target) ||
		ts.isParenthesizedExpression(target) || ts.isNonNullExpression(target)) target = target.expression;
	if (ts.isIdentifier(target)) return [target.text];
	if (ts.isArrayLiteralExpression(target)) {
		return target.elements.flatMap((element) => targetNames(ts.isSpreadElement(element) ? element.expression : element));
	}
	if (ts.isObjectLiteralExpression(target)) {
		return target.properties.flatMap((property) => {
			if (ts.isShorthandPropertyAssignment(property)) return [property.name.text];
			if (ts.isPropertyAssignment(property)) return targetNames(property.initializer);
			return ts.isSpreadAssignment(property) ? targetNames(property.expression) : [];
		});
	}
	return [];
}

/** The variables that `node` assigns, when it is an assignment, `++` or `--`. */
function assigned(node) {
	if (ts.isBinaryExpression(node) && node.operatorToken.kind >= ts.SyntaxKind.FirstAssignment &&
		node.operatorToken.kind <= ts.SyntaxKind.LastAssignment) return targetNames(node.left);
	if ((ts.isPrefixUnaryExpression(node) || ts.isPostfixUnaryExpression(node)) &&
		[ts.SyntaxKind.PlusPlusToken, ts.SyntaxKind.MinusMinusToken].includes(node.operator)) return targetNames(node.operand);
	return [];
}

/** The store that `node` changes when it is `store.set(...)` or `store.update(...)`. */
function storeCall(node, stores) {
	if (!ts.isCallExpression(node) || !ts.isPropertyAccessExpression(node.expression)) return null;
	const { expression: object, name } = node.expression;
	return ts.isIdentifier(object) && ['set', 'update'].includes(name.text) && stores.has(object.text) ? object.text : null;
}

/**
 * What running `start` (a function or a statement) does, following the functions of its file that
 * it names: its first use of the API (null for none), the variables it assigns and the stores it
 * changes, each with the functions it goes through.
 */
function walk(start, { imports, functions, stores }, via = []) {
	const result = { api: null, writes: new Map(), stores: new Map() };
	const seen = new Set([start]);
	const queue = [{ node: start, via }];
	while (queue.length > 0) {
		const { node, via } = queue.shift();
		const visit = (child) => {
			if (ts.isIdentifier(child) && isReference(child)) {
				const name = child.text;
				if (imports.has(name)) result.api ??= { name, module: imports.get(name), via };
				else if (name === 'fetch') result.api ??= { name, module: 'the browser', via };
				else if (functions.has(name) && !seen.has(functions.get(name))) {
					seen.add(functions.get(name));
					queue.push({ node: functions.get(name), via: [...via, name] });
				}
			}
			for (const name of assigned(child)) {
				const [map, key] = name.length > 1 && name.startsWith('$') ? [result.stores, name.slice(1)] : [result.writes, name];
				if (!map.has(key)) map.set(key, via);
			}
			const store = storeCall(child, stores);
			if (store && !result.stores.has(store)) result.stores.set(store, via);
			// obj.method(), where a class of src/ has a method of that name that reaches the API.
			if (ts.isCallExpression(child) && ts.isPropertyAccessExpression(child.expression)) {
				const found = classMethods().get(child.expression.name.text);
				if (found) result.api ??= { name: `${child.expression.expression.getText()}.${child.expression.name.text}`, module: found.module, via };
			}
			ts.forEachChild(child, visit);
		};
		ts.forEachChild(node, visit);
	}
	return result;
}

let methods = null;

/**
 * The methods of the classes of src/'s .ts modules (a page's state in a .svelte.ts module,
 * ADR-0425) that reach the API, by name, each with its class's file and what it reaches: methods,
 * properties whose value is a function, and getters; through each other too (this.load()). By
 * name, whatever the object it is called on: the check has no types.
 */
function classMethods() {
	if (methods !== null) return methods;
	methods = new Map();
	const candidates = [];
	for (const file of sourceFiles().filter((name) => name.endsWith('.ts'))) {
		const sourceFile = parse(readFileSync(file, 'utf8'));
		const context = scriptContext(sourceFile, file);
		const visit = (node) => {
			if (ts.isClassDeclaration(node) || ts.isClassExpression(node)) {
				for (const member of node.members) {
					if (!member.name || !ts.isIdentifier(member.name)) continue;
					const body = ts.isMethodDeclaration(member) || ts.isGetAccessorDeclaration(member) ? member.body
						: ts.isPropertyDeclaration(member) && isFunction(member.initializer) ? member.initializer : null;
					if (body) candidates.push({ name: member.name.text, body, file, context });
				}
			}
			ts.forEachChild(node, visit);
		};
		visit(sourceFile);
	}
	for (let grown = true; grown;) {
		grown = false;
		for (const { name, body, file, context } of candidates) {
			if (methods.has(name)) continue;
			const found = walk(body, context).api;
			if (!found) continue;
			methods.set(name, { module: `${relative(file)}, which reaches ${describe(found)}` });
			grown = true;
		}
	}
	return methods;
}

const NOTHING = { api: new Map(), stores: new Set(), apiModules: [] };
const modules = new Map();

/** What `info`, a module's exports, gives for the name `name`: its use of the API, or null. */
function lookup(info, name) {
	if (name === '*') return info.api.values().next().value ?? (info.apiModules.length > 0 ? { name, module: info.apiModules[0], via: [] } : null);
	return info.api.get(name) ?? (info.apiModules.length > 0 && name !== 'default' ? { name, module: info.apiModules[0], via: [] } : null);
}

/**
 * What the .ts module `file` of src/ exports that matters here: its functions that reach the API,
 * each with its first use of it (api), its stores (stores), and the API modules it re-exports
 * whole (apiModules). In a cycle of imports, a module being read gives nothing.
 */
function moduleInfo(file) {
	if (modules.has(file)) return modules.get(file) ?? NOTHING;
	modules.set(file, null);
	const sourceFile = parse(readFileSync(file, 'utf8'));
	const context = scriptContext(sourceFile, file);
	const info = { api: new Map(), stores: new Set(), apiModules: [] };
	const exportedFunctions = [];
	const exportLocal = (local, exported) => {
		if (context.imports.has(local)) info.api.set(exported, { name: local, module: context.imports.get(local), via: [] });
		else if (context.functions.has(local)) {
			exportedFunctions.push([exported, context.functions.get(local), local === exported ? [] : [local]]);
		}
		if (context.stores.has(local)) info.stores.add(exported);
	};
	for (const statement of sourceFile.statements) {
		const exported = hasModifier(statement, ts.SyntaxKind.ExportKeyword);
		if (ts.isFunctionDeclaration(statement) && exported && statement.body) {
			const name = hasModifier(statement, ts.SyntaxKind.DefaultKeyword) ? 'default' : statement.name?.text;
			if (name) exportedFunctions.push([name, statement, []]);
		} else if (ts.isVariableStatement(statement) && exported) {
			for (const declaration of statement.declarationList.declarations) {
				if (ts.isIdentifier(declaration.name)) exportLocal(declaration.name.text, declaration.name.text);
			}
		} else if (ts.isExportAssignment(statement) && !statement.isExportEquals) {
			if (ts.isIdentifier(statement.expression)) exportLocal(statement.expression.text, 'default');
			else if (isFunction(statement.expression)) exportedFunctions.push(['default', statement.expression, []]);
		} else if (ts.isExportDeclaration(statement) && !statement.isTypeOnly) {
			const clause = statement.exportClause;
			const elements = clause && ts.isNamedExports(clause) ? clause.elements.filter((element) => !element.isTypeOnly) : [];
			const specifier = statement.moduleSpecifier?.text;
			if (specifier === undefined) {
				for (const element of elements) exportLocal((element.propertyName ?? element.name).text, element.name.text);
			} else if (isApiModule(specifier, file)) {
				if (!clause) info.apiModules.push(specifier);
				else if (ts.isNamespaceExport(clause)) info.api.set(clause.name.text, { name: '*', module: specifier, via: [] });
				for (const element of elements) {
					info.api.set(element.name.text, { name: (element.propertyName ?? element.name).text, module: specifier, via: [] });
				}
			} else {
				const target = sourceModule(specifier, file);
				if (!target) continue;
				const other = moduleInfo(target);
				if (!clause) {
					for (const [name, found] of other.api) if (name !== 'default') info.api.set(name, found);
					for (const name of other.stores) if (name !== 'default') info.stores.add(name);
					info.apiModules.push(...other.apiModules);
				} else if (ts.isNamespaceExport(clause)) {
					const found = lookup(other, '*');
					if (found) info.api.set(clause.name.text, found);
				}
				for (const element of elements) {
					const name = (element.propertyName ?? element.name).text;
					const found = lookup(other, name);
					if (found) info.api.set(element.name.text, found);
					if (other.stores.has(name)) info.stores.add(element.name.text);
				}
			}
		}
	}
	for (const [name, node, via] of exportedFunctions) {
		const found = walk(node, context, via).api;
		if (found) info.api.set(name, found);
	}
	modules.set(file, info);
	return info;
}

/**
 * What a script of `file` can reach: the names it imports that are API, each with where it comes
 * from (imports); the functions it declares (functions); and the stores it uses (stores), also the
 * ones that the markup of a component (`markup`) reads as `$name`.
 */
function scriptContext(sourceFile, file, markup = '') {
	const imports = new Map();
	const stores = new Set();
	for (const statement of sourceFile.statements) {
		if (!ts.isImportDeclaration(statement) || !statement.importClause || statement.importClause.isTypeOnly) continue;
		const clause = statement.importClause;
		const bindings = clause.namedBindings;
		const specifier = statement.moduleSpecifier.text;
		let take;
		if (isApiModule(specifier, file)) {
			take = (local) => imports.set(local, specifier);
		} else {
			const target = sourceModule(specifier, file);
			if (!target) continue;
			const other = moduleInfo(target);
			take = (local, name) => {
				const found = lookup(other, name);
				if (found) imports.set(local, `${specifier}, which reaches ${describe(found)}`);
				if (other.stores.has(name)) stores.add(local);
			};
		}
		if (clause.name) take(clause.name.text, 'default');
		if (bindings && ts.isNamespaceImport(bindings)) take(bindings.name.text, '*');
		if (bindings && ts.isNamedImports(bindings)) {
			for (const element of bindings.elements) {
				if (!element.isTypeOnly) take(element.name.text, (element.propertyName ?? element.name).text);
			}
		}
	}
	const visit = (node) => {
		if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer &&
			ts.isCallExpression(node.initializer) && ts.isIdentifier(node.initializer.expression) &&
			STORE_FACTORIES.has(node.initializer.expression.text)) stores.add(node.name.text);
		if (ts.isIdentifier(node) && node.text.length > 1 && node.text.startsWith('$') && !RUNES.has(node.text)) {
			stores.add(node.text.slice(1));
		}
		ts.forEachChild(node, visit);
	};
	visit(sourceFile);
	for (const match of markup.matchAll(/\$([A-Za-z_]\w*)/g)) if (!RUNES.has(match[0])) stores.add(match[1]);
	return { imports, functions: localFunctions(sourceFile), stores };
}

/** The rune that `node` calls (`$effect`, `$derived.by`), or null. */
function runeOf(node) {
	if (!node || !ts.isCallExpression(node)) return null;
	const callee = node.expression;
	if (ts.isIdentifier(callee) && RUNES.has(callee.text)) return callee.text;
	if (ts.isPropertyAccessExpression(callee) && ts.isIdentifier(callee.expression) && RUNES.has(callee.expression.text)) {
		return `${callee.expression.text}.${callee.name.text}`;
	}
	return null;
}

/** The names that a binding (`a`, `{ a, b: c = 1, ...rest }`, `[a, b]`) declares. */
function boundNames(name) {
	if (ts.isIdentifier(name)) return [name.text];
	return name.elements.flatMap((element) => (ts.isOmittedExpression(element) ? [] : boundNames(element.name)));
}

/** The variables of the component's state: declared with $state, $state.raw, $derived or $props(). */
function stateVariables(sourceFile) {
	const names = new Set();
	for (const statement of sourceFile.statements) {
		if (!ts.isVariableStatement(statement)) continue;
		for (const declaration of statement.declarationList.declarations) {
			const rune = runeOf(declaration.initializer);
			if (rune && /^\$(state|derived|props)\b/.test(rune)) for (const name of boundNames(declaration.name)) names.add(name);
		}
	}
	return names;
}

const isUntrack = (node) => ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === 'untrack';

/**
 * The variables of `state` that running `start` reads, as Svelte tracks them: in its body and in
 * the functions of its file that it names, again and again, but not inside untrack(...); and the
 * variables it assigns.
 */
function reads(start, { functions }, state) {
	const names = new Set();
	const writes = new Set();
	const seen = new Set([start]);
	const queue = [start];
	while (queue.length > 0) {
		const visit = (node) => {
			if (isUntrack(node)) return;
			// name = value assigns name without reading it; name += 1 reads it.
			if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.EqualsToken &&
				ts.isIdentifier(node.left)) {
				writes.add(node.left.text);
				visit(node.right);
				return;
			}
			if (ts.isIdentifier(node) && isReference(node)) {
				if (state.has(node.text)) names.add(node.text);
				const local = functions.get(node.text);
				if (local && !seen.has(local)) {
					seen.add(local);
					queue.push(local);
				}
			}
			for (const name of assigned(node)) writes.add(name);
			ts.forEachChild(node, visit);
		};
		ts.forEachChild(queue.shift(), visit);
	}
	// Svelte 5 runs an effect again for a variable that it read, also one that it assigns.
	return { names: [...names], writes };
}

/**
 * The variables whose change makes an `$effect` of the script reach the API, each with that
 * effect's line and rune, its use of the API and the `$derived` variables between them (chain).
 */
function reactiveTriggers(sourceFile, context, lineOf) {
	const state = stateVariables(sourceFile);
	// What runs again: every $effect of the script, wherever it is and also given by name, and
	// every $derived, whose value is read again.
	const reactive = [];
	const derived = [];
	const visit = (node) => {
		const rune = runeOf(node);
		if (rune === '$effect' || rune === '$effect.pre') {
			const [given] = node.arguments;
			const run = given && ts.isIdentifier(given) ? context.functions.get(given.text) : given;
			if (run) reactive.push({ rune, run, line: lineOf(node), ...reads(run, context, state) });
		}
		if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name)) {
			const declared = runeOf(node.initializer);
			const [value] = node.initializer?.arguments ?? [];
			if ((declared === '$derived' || declared === '$derived.by') && value) {
				const { names } = reads(node.initializer, context, state);
				derived.push({ names, writes: new Set([node.name.text]) });
				reactive.push({ rune: '$derived', run: value, line: lineOf(node), names });
			}
		}
		ts.forEachChild(node, visit);
	};
	visit(sourceFile);
	const triggers = new Map();
	for (const { rune, run, line, names } of reactive) {
		const found = walk(run, context).api;
		if (!found) continue;
		for (const name of names) if (!triggers.has(name)) triggers.set(name, { rune, line, found, chain: [] });
	}
	for (let grown = true; grown;) {
		grown = false;
		for (const { names, writes } of derived) {
			const hit = [...writes].find((name) => triggers.has(name));
			if (hit === undefined) continue;
			const trigger = triggers.get(hit);
			for (const name of names) {
				if (triggers.has(name)) continue;
				triggers.set(name, { ...trigger, chain: [hit, ...trigger.chain] });
				grown = true;
			}
		}
	}
	return triggers;
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

/** What is wrong with a setInterval whose callback is `callback`; null for nothing. */
function problemOf(callback, context, triggers) {
	let start = callback;
	const via = [];
	if (ts.isIdentifier(callback)) {
		if (context.imports.has(callback.text)) {
			return `setInterval reaches the API: ${describe({ name: callback.text, module: context.imports.get(callback.text), via })}`;
		}
		start = context.functions.get(callback.text);
		via.push(callback.text);
	}
	if (!start || !(isFunction(start) || ts.isFunctionDeclaration(start))) {
		return `setInterval calls ${callback.getText()}, which the check cannot see into: ` +
			'give it a function literal or a function of this file';
	}
	const result = walk(start, context, via);
	if (result.api) return `setInterval reaches the API: ${describe(result.api)}`;
	for (const [store, via] of result.stores) {
		return `setInterval changes the store ${store}${through(via)}: what reads a store may reach the API, ` +
			'which the check cannot follow';
	}
	for (const [name, via] of result.writes) {
		const trigger = triggers.get(name);
		if (!trigger) continue;
		const chain = trigger.chain.length > 0 ? ` through ${trigger.chain.join(' -> ')}` : '';
		return `setInterval changes ${name}${through(via)}, which the ${trigger.rune} of line ${trigger.line} reads${chain}; ` +
			`it reaches the API: ${describe(trigger.found)}`;
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
	const markup = file.endsWith('.svelte') ? source.replace(SCRIPT, '') : '';
	for (const script of scripts(file, source)) {
		const sourceFile = parse(script.body);
		const lineOf = (node) => source.slice(0, script.offset + node.getStart(sourceFile)).split('\n').length;
		const context = scriptContext(sourceFile, file, markup);
		const triggers = file.endsWith('.svelte') || file.endsWith('.svelte.ts')
			? reactiveTriggers(sourceFile, context, lineOf)
			: new Map();
		const visit = (node) => {
			if (isSetInterval(node)) {
				timers += 1;
				const at = `${relative(file)}:${lineOf(node)}`;
				if (exempted(node, sourceFile)) {
					exempt += 1;
				} else if (node.arguments.length === 0) {
					problems.push(`${at}: setInterval without a callback`);
				} else {
					const problem = problemOf(node.arguments[0], context, triggers);
					if (problem) problems.push(`${at}: ${problem}`);
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
		'polling the API (ADR-0006, ADR-0365).');
	process.exit(1);
}
console.log(`polling: ${files.length} files, ${timers} setInterval call(s), none reaches the API ` +
	`(${exempt} exempted with polling-ignore)`);
