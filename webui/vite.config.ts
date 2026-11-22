import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

/**
 * Every component of src/ is compiled in runes mode, so that one written for Svelte 4's legacy
 * mode (export let, $:) fails the build (ADR-0427); scripts/runes-check.mjs fails for the syntax
 * of Svelte 4 that runes mode still takes. A library under node_modules keeps the mode it was
 * written in: lucide-svelte ships components of Svelte 4.
 */
export function compileOptions({ filename }: { filename: string }) {
	return filename.split(/[/\\]/).includes('node_modules') ? undefined : { runes: true };
}

// SvelteKit 3 takes its configuration here, in its Vite plugin, and no longer from
// svelte.config.js (ADR-0456). Tailwind CSS 4 builds the styles of src/app.css.
export default defineConfig({
	plugins: [
		tailwindcss(),
		sveltekit({
			preprocess: vitePreprocess(),
			dynamicCompileOptions: compileOptions,
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
			// SvelteKit 3 no longer provides $lib (its subpath import is #lib): the web UI keeps it.
			alias: {
				$lib: './src/lib'
			}
		})
	],
	server: {
		proxy: {
			'/api': 'http://localhost:8080',
			'/ws': {
				target: 'ws://localhost:8080',
				ws: true
			}
		}
	}
});
