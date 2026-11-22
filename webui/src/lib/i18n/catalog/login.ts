import type { Translation } from '../translate';

// The sign-in page: src/routes/login/+page.svelte.

export const en = {
	'login.recoveryTitle': 'Recovery code',
	'login.recoveryHint': 'Enter one of your recovery codes. Each code works only once.',
	'login.recoveryLabel': 'Recovery code',
	'login.recoveryPlaceholder': 'XXXX-XXXX-XXXX',
	'login.totpTitle': 'Two-factor authentication',
	'login.totpHint': 'Enter the 6-digit code from your authenticator app',
	'login.totpLabel': 'Authentication code',
	'login.useAppCode': 'Use a code from your authenticator app',
	'login.useRecoveryCode': 'Use a recovery code',
	'login.back': '← Back to login',
	'login.verify': 'Verify',
	'login.signIn': 'Sign in',
	// The button beside the password's label that shows or hides it.
	'login.showPassword': 'Show',
	'login.hidePassword': 'Hide',
	'login.failed': 'Login failed',
	'login.signOutFailed':
		'The NAS did not confirm the sign-out, so this browser may still be signed in. Sign out again once the NAS can be reached.',
	'login.signOutAgain': 'Sign out again'
};

export const ru: Translation<typeof en> = {
	'login.recoveryTitle': 'Код восстановления',
	'login.recoveryHint': 'Введите один из своих кодов восстановления. Каждый код действует только один раз.',
	'login.recoveryLabel': 'Код восстановления',
	'login.recoveryPlaceholder': 'XXXX-XXXX-XXXX',
	'login.totpTitle': 'Двухфакторная аутентификация',
	'login.totpHint': 'Введите 6-значный код из приложения-аутентификатора',
	'login.totpLabel': 'Код подтверждения',
	'login.useAppCode': 'Ввести код из приложения-аутентификатора',
	'login.useRecoveryCode': 'Ввести код восстановления',
	'login.back': '← Назад ко входу',
	'login.verify': 'Подтвердить',
	'login.signIn': 'Войти',
	'login.showPassword': 'Показать',
	'login.hidePassword': 'Скрыть',
	'login.failed': 'Не удалось войти',
	'login.signOutFailed':
		'NAS не подтвердил выход, поэтому в этом браузере сеанс, возможно, ещё активен. Выйдите ещё раз, когда NAS станет доступен.',
	'login.signOutAgain': 'Выйти ещё раз'
};
