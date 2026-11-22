import type { Translation } from '../translate';

// The theme of the web UI on the Account page (src/lib/components/account/ThemeCard.svelte,
// ADR-0405).

export const en = {
	'theme.title': 'Theme',
	'theme.label': 'Colour theme',
	'theme.dark': 'Dark',
	'theme.light': 'Light',
	'theme.system': 'System',
	'theme.hint':
		'Applies at once and is kept for your account, in every browser you sign in to. System follows the light or dark setting of your device.',
	'theme.saveFailed': 'The theme could not be saved',
	'theme.loadFailed': 'Your saved theme could not be loaded: this browser shows its own. Open this page again to retry.'
};

export const ru: Translation<typeof en> = {
	'theme.title': 'Тема',
	'theme.label': 'Цветовая тема',
	'theme.dark': 'Тёмная',
	'theme.light': 'Светлая',
	'theme.system': 'Системная',
	'theme.hint':
		'Применяется сразу и сохраняется для вашей учётной записи во всех браузерах, где вы входите в систему. Системная тема повторяет светлый или тёмный режим вашего устройства.',
	'theme.saveFailed': 'Не удалось сохранить тему',
	'theme.loadFailed':
		'Не удалось загрузить сохранённую тему: браузер показывает свою. Откройте страницу снова, чтобы повторить.'
};
