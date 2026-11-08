import type { Translation } from '../translate';

// The sign-in page: src/routes/login/+page.svelte.

export const en = {
	'login.recoveryTitle': 'Recovery Code',
	'login.recoveryHint': 'Enter one of your recovery codes. Each code works only once.',
	'login.recoveryLabel': 'Recovery code',
	'login.recoveryPlaceholder': 'XXXX-XXXX-XXXX',
	'login.totpTitle': 'Two-Factor Authentication',
	'login.totpHint': 'Enter the 6-digit code from your authenticator app',
	'login.totpLabel': 'Authentication Code',
	'login.useAppCode': 'Use a code from your authenticator app',
	'login.useRecoveryCode': 'Use a recovery code',
	'login.back': '← Back to login',
	'login.signingIn': 'Signing in...',
	'login.verify': 'Verify',
	'login.signIn': 'Sign In',
	'login.failed': 'Login failed'
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
	'login.signingIn': 'Вход...',
	'login.verify': 'Подтвердить',
	'login.signIn': 'Войти',
	'login.failed': 'Не удалось войти'
};
