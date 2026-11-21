// ESLint for the web UI's code, run by `npm run lint` over src/ (make frontend, ADR-0448): the
// recommended rules of ESLint, typescript-eslint and eslint-plugin-svelte. A warning fails the run
// too (--max-warnings 0), and so does an exemption that exempts nothing. A rule turned off here, or
// a line exempted with `// eslint-disable-next-line <rule> -- <reason>`, gives its reason.
// Formatting is Prettier's (prettier.config.js), whose check runs beside it.
import js from '@eslint/js';
import { defineConfig } from 'eslint/config';
import svelte from 'eslint-plugin-svelte';
import ts from 'typescript-eslint';

export default defineConfig(
	js.configs.recommended,
	ts.configs.recommended,
	svelte.configs.recommended,
	{
		linterOptions: { reportUnusedDisableDirectives: 'error' }
	},
	{
		files: ['**/*.svelte', '**/*.svelte.ts'],
		languageOptions: {
			parserOptions: {
				parser: ts.parser,
				// The scripts of components in TypeScript, with its types for the rules that read them.
				projectService: true,
				extraFileExtensions: ['.svelte'],
				// svelte.config.js compiles every file of src/ in runes mode (ADR-0427). The parser
				// reads the rest of that file itself, as far as it can without running it: the module
				// passed in would put its functions into the configuration, which --cache and
				// --print-config cannot store.
				svelteFeatures: { runes: true }
			}
		}
	},
	{
		rules: {
			// TypeScript, which svelte-check runs over every file, reports a name that is not
			// defined, and knows the browser's and SvelteKit's globals; typescript-eslint advises
			// this rule off for TypeScript.
			'no-undef': 'off',
			// A key changes which rows Svelte keeps when a list changes, and a key that two items
			// share throws (each_key_duplicate), as two equal warnings of a list would: a key is
			// added where a list needs one, with its test, not to every {#each} at once.
			'svelte/require-each-key': 'off',
			// The web UI is served at the root of its origin, without kit.paths.base: resolve()
			// would return every path as it is. Many links are data (an item's href, a release's
			// page, a hash), which resolve() does not take.
			'svelte/no-navigation-without-resolve': 'off',
			// A Set or a Map is replaced whole, not changed inside (webui/AGENTS.md), and the
			// bookkeeping of timers stays plain; the rule asks for SvelteSet or SvelteMap at every
			// new Set or Map of a component or a .svelte.ts module, also a local one only read.
			'svelte/prefer-svelte-reactivity': 'off',
			// Svelte 5 drops the whitespace at the edges of an element, a block and an {#each}'s
			// iteration: a space or a separator there is written as a string ({' '}, {' · '}),
			// which this rule takes for useless. The text check (npm run i18n) refuses any other
			// literal text in the markup.
			'svelte/no-useless-mustaches': 'off'
		}
	}
);
