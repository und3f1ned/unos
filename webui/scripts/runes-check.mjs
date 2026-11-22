#!/usr/bin/env node
// Every component of the web UI is a Svelte 5 component in runes mode, none in the legacy mode
// of Svelte 4 (ADR-0427). npm run check runs this check after svelte-check;
// supervisor/tests/test_webui_runes.py tests it.
//
//   node scripts/runes-check.mjs [webui directory]      (default: the webui/ of this script)
//
// Each .svelte file of src/ is compiled as vite.config.ts builds it, in runes mode, and fails
// - when it is not in runes mode after all (`<svelte:options runes={false} />`);
// - for what runes mode refuses: `export let`, `$:`, `$$props`, `$$restProps`, beforeUpdate;
// - for the syntax of Svelte 4 that runes mode still takes: `on:` directives, also on a
//   component (onclick={...}, and callback props instead of createEventDispatcher), <slot>,
//   $$slots and `let:` (snippets and {@render}), a `slot` attribute and <svelte:fragment> (a
//   {#snippet} in the component), <svelte:component> (a component in a variable), <svelte:self>
//   (the component imported by its own name), <script context="module"> (<script module>) and
//   the options accessors and immutable;
// - for a variable that changes and that the markup shows but that is no $state
//   (non_reactive_update: in runes mode a plain `let` is not reactive);
// - for the imports of svelte/legacy, of createEventDispatcher and of $app/stores ($app/state),
//   also as import(...).
// The .ts modules of src/ fail for the imports of svelte/legacy and $app/stores too.

import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { compile, parse } from 'svelte/compiler';
import { walk } from './svelte-walk.mjs';

const WEBUI = path.resolve(process.argv[2] ?? path.join(path.dirname(fileURLToPath(import.meta.url)), '..'));
const SRC = path.join(WEBUI, 'src');
const relative = (file) => path.relative(WEBUI, file).split(path.sep).join('/');

// The warnings of runes mode for the syntax of Svelte 4, with what replaces it.
const DEPRECATED = {
	slot_element_deprecated: '<slot>: use a snippet prop and {@render ...}',
	svelte_component_deprecated: '<svelte:component>: use the component in a variable',
	svelte_self_deprecated: '<svelte:self>: import the component by its own name',
	script_context_deprecated: '<script context="module">: use <script module>',
	options_deprecated_accessors: 'the option accessors: export what the parent needs',
	options_deprecated_immutable: 'the option immutable: runes mode compares by value',
	non_reactive_update: 'a variable that changes and that the markup shows: declare it with $state'
};
// Imports of Svelte 4's ways, with what replaces them.
const IMPORTS = {
	'svelte/legacy': 'svelte/legacy: use event.preventDefault() and the like, $derived or $effect',
	'$app/stores': '$app/stores: use $app/state'
};
const LEGACY_FROM_SVELTE = { createEventDispatcher: 'createEventDispatcher: use a callback prop (onclose, onsaved)' };

/** The line and column (from 1) of `offset` in `text`. */
function position(text, offset) {
	const before = text.slice(0, offset).split('\n');
	return `${before.length}:${before.at(-1).length + 1}`;
}

/** The source with the content of every <style> blanked out: the check reads scripts and markup. */
const withoutStyles = (source) =>
	source.replace(/(<style\b[^>]*>)([\s\S]*?)(<\/style>)/g, (_, open, body, close) => open + body.replace(/[^\n]/g, ' ') + close);

function files(extension) {
	return readdirSync(SRC, { recursive: true })
		.filter((name) => name.endsWith(extension) && !name.endsWith('.d.ts'))
		.map((name) => path.join(SRC, name))
		.sort();
}

const errors = [];

function checkComponent(file) {
	const name = relative(file);
	const source = withoutStyles(readFileSync(file, 'utf8'));
	const report = (offset, text) => errors.push(`${name}:${position(source, offset)}: ${text}`);

	let result;
	try {
		result = compile(source, { filename: name, runes: true, generate: false });
	} catch (error) {
		const at = error.start ? `${error.start.line}:${error.start.column + 1}` : '1:1';
		errors.push(`${name}:${at}: not a component of runes mode: ${error.message.split('\n')[0]}`);
		return;
	}
	if (result.metadata.runes !== true) {
		errors.push(`${name}:1:1: not in runes mode (<svelte:options runes={false} />): remove the option`);
	}
	for (const warning of result.warnings) {
		if (!(warning.code in DEPRECATED)) continue;
		errors.push(`${name}:${warning.start.line}:${warning.start.column + 1}: ${DEPRECATED[warning.code]}`);
	}

	const ast = parse(source, { modern: true });
	walk(ast.fragment, {
		enter(node) {
			if (node.type === 'SvelteFragment') report(node.start, '<svelte:fragment>: use a {#snippet} in the component');
			if (node.type === 'OnDirective') report(node.start, 'an on: directive: use an event attribute (onclick={...}) or a callback prop');
			if (node.type === 'LetDirective') report(node.start, 'a let: directive: take the value as a parameter of a {#snippet}');
			if (Array.isArray(node.attributes)) {
				for (const attribute of node.attributes) {
					if (attribute.type === 'Attribute' && attribute.name === 'slot') {
						report(attribute.start, 'a slot attribute: use a {#snippet} in the component');
					}
				}
			}
		}
	});
	for (const match of source.matchAll(/\$\$slots\b/g)) report(match.index, '$$slots: use the snippet props');
	for (const match of source.matchAll(/\bimport\s*\(\s*['"](svelte\/legacy|\$app\/stores)['"]/g)) {
		report(match.index, `an import of ${IMPORTS[match[1]]}`);
	}
	for (const script of [ast.instance, ast.module]) {
		if (!script) continue;
		for (const statement of script.content.body) {
			if (statement.type !== 'ImportDeclaration') continue;
			const from = statement.source.value;
			if (from in IMPORTS) report(statement.start, `an import of ${IMPORTS[from]}`);
			if (from !== 'svelte') continue;
			for (const specifier of statement.specifiers) {
				const imported = specifier.imported?.name;
				if (imported in LEGACY_FROM_SVELTE) report(specifier.start, `an import of ${LEGACY_FROM_SVELTE[imported]}`);
			}
		}
	}
}

// An import of a module by name: `from '...'`, `import '...'` or `import('...')`.
const LEGACY_IMPORT = /\b(?:from\s*|import\s*\(?\s*)['"](svelte\/legacy|\$app\/stores)['"]/g;

function checkModule(file) {
	const name = relative(file);
	const source = readFileSync(file, 'utf8');
	for (const match of source.matchAll(LEGACY_IMPORT)) {
		errors.push(`${name}:${position(source, match.index)}: an import of ${IMPORTS[match[1]]}`);
	}
}

const components = files('.svelte');
for (const file of components) checkComponent(file);
for (const file of [...files('.ts'), ...files('.js')]) checkModule(file);

if (errors.length > 0) {
	for (const error of errors) console.error(error);
	console.error(`runes: ${errors.length} problem(s). Every component is in runes mode, without the syntax of ` +
		'Svelte 4 (webui/AGENTS.md, ADR-0427).');
	process.exit(1);
}
console.log(`runes: ${components.length} .svelte files compile in runes mode, with no syntax of Svelte 4 left`);
