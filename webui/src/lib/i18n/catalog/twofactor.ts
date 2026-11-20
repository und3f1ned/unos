import type { Translation } from '../translate';

// Two-factor authentication for every account: the page that asks an account to set it up
// (src/routes/two-factor-setup/+page.svelte), the administrator's card in System > Security
// (src/lib/components/system/security/TwoFactorRequirementCard.svelte) and the Account page's
// warning.

export const en = {
	'twofactor.title': 'Set up two-factor authentication',
	'twofactor.due':
		'An administrator requires two-factor authentication for every account. Set it up by {date}: after that, your account can only set it up.',
	'twofactor.setupOnly':
		'An administrator requires two-factor authentication for every account, and the time to set it up has ended: your account can do nothing else until it is set up.',
	'twofactor.appHint':
		'You need an authenticator app on your phone or computer, such as Google Authenticator, Authy or FreeOTP.',
	'twofactor.start': 'Set up now',
	'twofactor.later': 'Later',
	'twofactor.signOut': 'Sign out',
	'twofactor.done': 'Two-factor authentication is on',
	'twofactor.doneHint':
		'Generate recovery codes now: each one signs you in once if you lose your authenticator app.',
	'twofactor.recoveryCodes': 'Generate recovery codes',
	'twofactor.continue': 'Continue',

	'twofactor.requirement.title': 'Two-factor authentication for every account',
	'twofactor.requirement.on': 'Required',
	'twofactor.requirement.off': 'Not required',
	'twofactor.requirement.offText': 'Each user decides whether to use two-factor authentication.',
	'twofactor.requirement.since': 'Required since {date}, turned on by {name}.',
	'twofactor.requirement.sinceUnknown':
		'Required. When it was turned on cannot be read from the database: accounts without two-factor authentication get no grace period.',
	'twofactor.requirement.grace': {
		one: 'Grace period: {n} day from that time or from the account’s creation, whichever is later.',
		other: 'Grace period: {n} days from that time or from the account’s creation, whichever is later.'
	},
	'twofactor.requirement.noGrace': 'No grace period.',
	'twofactor.requirement.hint':
		'Accounts without two-factor authentication are asked to set it up at their next sign-in, and after the grace period can only set it up; their API tokens stop working until then. SMB has no second factor: SMB access still signs in with the password alone.',
	'twofactor.requirement.ownFirst':
		'Turn on two-factor authentication for your own account first: turning the requirement on or off takes a code from your authenticator app.',
	'twofactor.requirement.graceLabel': 'Grace period',
	'twofactor.requirement.graceNone': 'None: at once',
	'twofactor.requirement.graceDays': { one: '{n} day', other: '{n} days' },
	'twofactor.requirement.codeLabel': 'Code from your authenticator app',
	'twofactor.requirement.turnOn': 'Require for every account',
	'twofactor.requirement.turnOff': 'Stop requiring',
	'twofactor.requirement.turnedOn': 'Two-factor authentication is now required for every account',
	'twofactor.requirement.turnedOff': 'Two-factor authentication is no longer required for every account',
	'twofactor.requirement.loadError': 'Could not load whether every account must have two-factor authentication',

	'twofactor.disableRequired':
		'Every account must have two-factor authentication: once it is off you have to set it up again, and after your grace period your account can do nothing else until you do.'
};

export const ru: Translation<typeof en> = {
	'twofactor.title': 'Настройка двухфакторной аутентификации',
	'twofactor.due':
		'Администратор требует двухфакторную аутентификацию для всех учётных записей. Настройте её до {date}: после этого срока вашей учётной записи будет доступна только её настройка.',
	'twofactor.setupOnly':
		'Администратор требует двухфакторную аутентификацию для всех учётных записей, и срок на её настройку истёк: пока она не настроена, вашей учётной записи доступна только настройка.',
	'twofactor.appHint':
		'Понадобится приложение-аутентификатор на телефоне или компьютере, например Google Authenticator, Authy или FreeOTP.',
	'twofactor.start': 'Настроить сейчас',
	'twofactor.later': 'Позже',
	'twofactor.signOut': 'Выйти',
	'twofactor.done': 'Двухфакторная аутентификация включена',
	'twofactor.doneHint':
		'Создайте коды восстановления сейчас: каждый из них один раз позволит войти, если приложение-аутентификатор потеряно.',
	'twofactor.recoveryCodes': 'Создать коды восстановления',
	'twofactor.continue': 'Продолжить',

	'twofactor.requirement.title': 'Двухфакторная аутентификация для всех учётных записей',
	'twofactor.requirement.on': 'Обязательна',
	'twofactor.requirement.off': 'Не обязательна',
	'twofactor.requirement.offText': 'Каждый пользователь сам решает, включать ли двухфакторную аутентификацию.',
	'twofactor.requirement.since': 'Обязательна с {date}, включил(а) {name}.',
	'twofactor.requirement.sinceUnknown':
		'Обязательна. Когда её включили, прочитать из базы данных не удалось: учётным записям без двухфакторной аутентификации срок на настройку не даётся.',
	'twofactor.requirement.grace': {
		one: 'Срок на настройку: {n} день с этого момента или с создания учётной записи, если она создана позже.',
		few: 'Срок на настройку: {n} дня с этого момента или с создания учётной записи, если она создана позже.',
		many: 'Срок на настройку: {n} дней с этого момента или с создания учётной записи, если она создана позже.'
	},
	'twofactor.requirement.noGrace': 'Без срока на настройку.',
	'twofactor.requirement.hint':
		'Учётным записям без двухфакторной аутентификации при следующем входе предлагается её настроить, а после срока на настройку доступна только она; их API-токены до тех пор не работают. У SMB нет второго фактора: для доступа по SMB по-прежнему достаточно пароля.',
	'twofactor.requirement.ownFirst':
		'Сначала включите двухфакторную аутентификацию для своей учётной записи: чтобы включить или выключить требование, нужен код из приложения-аутентификатора.',
	'twofactor.requirement.graceLabel': 'Срок на настройку',
	'twofactor.requirement.graceNone': 'Без срока: сразу',
	'twofactor.requirement.graceDays': { one: '{n} день', few: '{n} дня', many: '{n} дней' },
	'twofactor.requirement.codeLabel': 'Код из приложения-аутентификатора',
	'twofactor.requirement.turnOn': 'Требовать для всех',
	'twofactor.requirement.turnOff': 'Не требовать',
	'twofactor.requirement.turnedOn': 'Двухфакторная аутентификация теперь обязательна для всех учётных записей',
	'twofactor.requirement.turnedOff': 'Двухфакторная аутентификация больше не обязательна для всех учётных записей',
	'twofactor.requirement.loadError':
		'Не удалось узнать, обязательна ли двухфакторная аутентификация для всех учётных записей',

	'twofactor.disableRequired':
		'Двухфакторная аутентификация обязательна для всех учётных записей: после выключения её нужно настроить снова, а если срок на настройку истёк, до этого вашей учётной записи будет доступна только настройка.'
};
