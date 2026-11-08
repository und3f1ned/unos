import type { Translation } from '../translate';

// The first-run setup page: src/routes/setup/+page.svelte.

export const en = {
	'setup.intro': 'Create your admin account to get started',
	'setup.confirmPassword': 'Confirm Password',
	'setup.mismatch': 'Passwords do not match',
	'setup.failed': 'Setup failed',
	'setup.creating': 'Creating account...',
	'setup.create': 'Create Admin Account'
};

export const ru: Translation<typeof en> = {
	'setup.intro': 'Создайте учётную запись администратора, чтобы начать работу',
	'setup.confirmPassword': 'Подтверждение пароля',
	'setup.mismatch': 'Пароли не совпадают',
	'setup.failed': 'Не удалось выполнить настройку',
	'setup.creating': 'Создание учётной записи...',
	'setup.create': 'Создать учётную запись администратора'
};
