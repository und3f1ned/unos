import type { Translation } from '../translate';

// The first-run setup page: src/routes/setup/+page.svelte.

export const en = {
	'setup.intro': 'Create your admin account to get started',
	'setup.confirmPassword': 'Confirm password',
	'setup.mismatch': 'Passwords do not match',
	'setup.failed': 'Setup failed',
	'setup.create': 'Create admin account',
	// Under the fields, and what is wrong with them once Create is pressed: the rules of the server
	// (validate_new_username and the password's length).
	'setup.usernameHint': '1–32 lowercase letters, digits, _ or -, starting with a letter',
	'setup.usernameRequired': 'Enter a username',
	'setup.usernameInvalid': 'Use 1–32 lowercase letters, digits, _ or -, starting with a letter',
	'setup.passwordHint': { one: 'At least {n} character', other: 'At least {n} characters' },
	'setup.passwordShort': { one: 'Use at least {n} character', other: 'Use at least {n} characters' }
};

export const ru: Translation<typeof en> = {
	'setup.intro': 'Создайте учётную запись администратора, чтобы начать работу',
	'setup.confirmPassword': 'Подтверждение пароля',
	'setup.mismatch': 'Пароли не совпадают',
	'setup.failed': 'Не удалось выполнить настройку',
	'setup.create': 'Создать учётную запись администратора',
	'setup.usernameHint': 'От 1 до 32 строчных латинских букв, цифр, _ или -, первая — буква',
	'setup.usernameRequired': 'Введите имя пользователя',
	'setup.usernameInvalid': 'Используйте от 1 до 32 строчных латинских букв, цифр, _ или -, первая — буква',
	'setup.passwordHint': { one: 'Не короче {n} символа', few: 'Не короче {n} символов', many: 'Не короче {n} символов' },
	'setup.passwordShort': {
		one: 'Нужен хотя бы {n} символ',
		few: 'Нужно хотя бы {n} символа',
		many: 'Нужно хотя бы {n} символов'
	}
};
