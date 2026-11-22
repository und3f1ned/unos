import type { Translation } from '../translate';

// Security keys (ADR-0409): the part of the Two-Factor Authentication card on the Account page
// (src/lib/components/account/SecurityKeys.svelte), the code step of the sign-in page
// (src/routes/login/+page.svelte) and what the browser reports ($lib/webauthn).

export const en = {
	'securitykeys.title': 'Security keys',
	'securitykeys.hint':
		'A security key, such as a YubiKey, signs you in with a touch in place of a code from your authenticator app. Keep the app: turning two-factor authentication off and turning SMB on still take its codes.',
	'securitykeys.needsTotp':
		'Security keys: turn on two-factor authentication with an authenticator app first. A security key then signs you in in place of its codes.',
	'securitykeys.ipAddress':
		'Security keys work only when the web UI is opened by the NAS’s name, such as https://unos.local, not by an IP address. Open it by its name to add or use a key.',
	'securitykeys.unsupported': 'This browser does not support security keys.',
	'securitykeys.none': 'No security keys yet.',
	'securitykeys.list': 'Your security keys',
	'securitykeys.added': 'Added {date}',
	'securitykeys.lastUsed': 'Last used {date}',
	'securitykeys.neverUsed': 'Not used yet',
	'securitykeys.worksAt': 'Works at {origin}',
	'securitykeys.unusable': 'Its stored data cannot be read: it signs in nowhere. Remove it.',
	'securitykeys.add': 'Add a security key',
	'securitykeys.remove': 'Remove',
	'securitykeys.removeLabel': 'Remove {name}',
	'securitykeys.addTitle': 'Add a security key',
	'securitykeys.addText':
		'Give the key a name you will recognise, enter your password, then touch the key when your browser asks.',
	'securitykeys.nameLabel': 'Name',
	'securitykeys.namePlaceholder': 'YubiKey on my key ring',
	'securitykeys.passwordLabel': 'Current password',
	'securitykeys.touch': 'Touch your security key, or follow your browser’s prompt.',
	'securitykeys.addButton': 'Add key',
	'securitykeys.nameTaken': 'You already have a security key with this name: choose another one.',
	'securitykeys.addedNotice': 'Security key “{name}” added.',
	'securitykeys.removeTitle': 'Remove security key',
	'securitykeys.removeText':
		'Remove “{name}”? It will no longer sign you in. Your authenticator app and your recovery codes still do.',
	'securitykeys.removeButton': 'Remove key',
	'securitykeys.removedNotice': 'Security key “{name}” removed.',
	'securitykeys.loadError': 'Your security keys could not be loaded',
	'securitykeys.error.cancelled': 'The security key was not used: the request was cancelled, or its time ran out.',
	'securitykeys.error.alreadyRegistered': 'This security key is already registered.',
	'securitykeys.error.address':
		'The browser refused security keys at this address. Open the web UI by the NAS’s name, with a certificate the browser trusts.',
	'securitykeys.error.unsupported': 'This browser or security key cannot do what the NAS asks for.',

	'securitykeys.login.use': 'Use a security key',
	'securitykeys.login.touch': 'Touch your security key…',
	'securitykeys.login.or': 'or enter the code from your authenticator app',
	'securitykeys.login.elsewhere': 'Your security keys work at {origins}. Open the web UI there to use one.'
};

export const ru: Translation<typeof en> = {
	'securitykeys.title': 'Ключи безопасности',
	'securitykeys.hint':
		'Ключ безопасности, например YubiKey, позволяет войти касанием вместо кода из приложения-аутентификатора. Не удаляйте приложение: для отключения двухфакторной аутентификации и включения SMB по-прежнему нужны его коды.',
	'securitykeys.needsTotp':
		'Ключи безопасности: сначала включите двухфакторную аутентификацию с приложением-аутентификатором. После этого ключ безопасности сможет заменять его коды при входе.',
	'securitykeys.ipAddress':
		'Ключи безопасности работают, только когда веб-интерфейс открыт по имени NAS, например https://unos.local, а не по IP-адресу. Откройте его по имени, чтобы добавить или использовать ключ.',
	'securitykeys.unsupported': 'Этот браузер не поддерживает ключи безопасности.',
	'securitykeys.none': 'Ключей безопасности пока нет.',
	'securitykeys.list': 'Ваши ключи безопасности',
	'securitykeys.added': 'Добавлен {date}',
	'securitykeys.lastUsed': 'Последний вход {date}',
	'securitykeys.neverUsed': 'Ещё не использовался',
	'securitykeys.worksAt': 'Работает на {origin}',
	'securitykeys.unusable': 'Сохранённые данные ключа не читаются: он нигде не позволяет войти. Удалите его.',
	'securitykeys.add': 'Добавить ключ безопасности',
	'securitykeys.remove': 'Удалить',
	'securitykeys.removeLabel': 'Удалить {name}',
	'securitykeys.addTitle': 'Добавление ключа безопасности',
	'securitykeys.addText': 'Дайте ключу понятное имя, введите свой пароль и коснитесь ключа, когда браузер попросит.',
	'securitykeys.nameLabel': 'Имя',
	'securitykeys.namePlaceholder': 'YubiKey на связке ключей',
	'securitykeys.passwordLabel': 'Текущий пароль',
	'securitykeys.touch': 'Коснитесь ключа безопасности или следуйте подсказке браузера.',
	'securitykeys.addButton': 'Добавить ключ',
	'securitykeys.nameTaken': 'У вас уже есть ключ безопасности с таким именем: выберите другое.',
	'securitykeys.addedNotice': 'Ключ безопасности «{name}» добавлен.',
	'securitykeys.removeTitle': 'Удаление ключа безопасности',
	'securitykeys.removeText':
		'Удалить «{name}»? С ним больше нельзя будет войти. Приложение-аутентификатор и коды восстановления по-прежнему работают.',
	'securitykeys.removeButton': 'Удалить ключ',
	'securitykeys.removedNotice': 'Ключ безопасности «{name}» удалён.',
	'securitykeys.loadError': 'Не удалось загрузить ваши ключи безопасности',
	'securitykeys.error.cancelled': 'Ключ безопасности не использован: запрос отменён или его время истекло.',
	'securitykeys.error.alreadyRegistered': 'Этот ключ безопасности уже зарегистрирован.',
	'securitykeys.error.address':
		'Браузер не разрешил использовать ключи безопасности на этом адресе. Откройте веб-интерфейс по имени NAS с сертификатом, которому доверяет браузер.',
	'securitykeys.error.unsupported': 'Этот браузер или ключ безопасности не поддерживает то, что требует NAS.',

	'securitykeys.login.use': 'Войти с ключом безопасности',
	'securitykeys.login.touch': 'Коснитесь ключа безопасности…',
	'securitykeys.login.or': 'или введите код из приложения-аутентификатора',
	'securitykeys.login.elsewhere':
		'Ваши ключи безопасности работают на {origins}. Откройте веб-интерфейс там, чтобы использовать ключ.'
};
