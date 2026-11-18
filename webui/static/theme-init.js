// The theme of the page before it is drawn (ADR-0405), so that it never shows the other theme first.
// app.html loads this file in its head; the CSP allows no inline script but SvelteKit's own. The
// choice is the one that $lib/stores/theme keeps in this browser ('unos-theme': dark, light or
// system), System when there is none: the device's prefers-color-scheme. The store then follows
// the choice of the signed-in user and the device's changes.
(function () {
	var choice = null;
	try {
		choice = localStorage.getItem('unos-theme');
	} catch (e) {
		// No storage: System.
	}
	var light =
		choice === 'light' ||
		(choice !== 'dark' &&
			typeof window.matchMedia === 'function' &&
			window.matchMedia('(prefers-color-scheme: light)').matches);
	var theme = light ? 'light' : 'dark';
	document.documentElement.setAttribute('data-theme', theme);
	// Until the style sheet has come, the browser paints its own canvas: of this theme too.
	document.documentElement.style.colorScheme = theme;
})();
