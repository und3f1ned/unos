import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	preprocess: vitePreprocess(),
	// Every component of src/ is compiled in runes mode, so that one written for Svelte 4's legacy
	// mode (export let, $:) fails the build (ADR-0427); scripts/runes-check.mjs fails for the
	// syntax of Svelte 4 that runes mode still takes. A library under node_modules keeps the mode
	// it was written in: lucide-svelte 0.x ships components of Svelte 4.
	vitePlugin: {
		dynamicCompileOptions: ({ filename }) =>
			filename.split(/[/\\]/).includes('node_modules') ? undefined : { runes: true }
	},
	kit: {
		adapter: adapter({
			pages: 'build',
			assets: 'build',
			fallback: 'index.html',
			precompress: false,
			strict: true
		}),
		// The built index.html starts the app with an inline <script>. SvelteKit
		// adds that script's sha256 hash to this policy and emits it as a
		// <meta http-equiv="content-security-policy"> tag, so only that exact
		// inline script may run. The hash changes with every build, which is
		// why it cannot live in the Caddyfile; Caddy sends the rest of the
		// policy as a header (see Caddyfile).
		csp: {
			mode: 'hash',
			directives: {
				'script-src': ['self'],
				'object-src': ['none'],
				'base-uri': ['self']
			}
		},
		alias: {
			'$lib': './src/lib'
		}
	}
};

export default config;
