// Prettier for the web UI's code, whose check `npm run format-check` runs over src/ (make frontend);
// `npm run format` writes it. The settings are the style the code had: tabs, single quotes, no
// trailing commas and lines of up to 120 columns (ADR-0449). Where a line break would change a
// text of Svelte 5, the element keeps its source with `<!-- prettier-ignore -->` and the reason.
/** @type {import('prettier').Config} */
const config = {
	useTabs: true,
	singleQuote: true,
	trailingComma: 'none',
	printWidth: 120,
	plugins: ['prettier-plugin-svelte'],
	overrides: [{ files: '*.svelte', options: { parser: 'svelte' } }]
};

export default config;
