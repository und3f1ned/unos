import type { Translation } from '../translate';

// The Users page: src/routes/users/+page.svelte. The server's messages (a refusal, the reason SMB
// is not available, the warnings of a change) stay in English (ADR-0287). A sentence with a
// user's name in bold is translated in parts around the name (ADR-0296).

export const en = {
	'users.title': 'Users',
	'users.add': 'Add user',
	'users.loadFailed': 'Failed to load users',
	'users.count': 'Users ({count})',
	'users.empty': 'No users found',
	'users.role': 'Role',
	'users.twoFactor': '2FA',
	'users.smb': 'SMB',
	'users.created': 'Created',
	'users.actions': 'Actions',
	'users.you': 'You',
	'users.admin': 'Admin',
	'users.user': 'User',
	'users.twoFactorOn': 'Enabled',
	'users.twoFactorOff': 'Disabled',
	'users.editUser': 'Edit user {name}',
	'users.deleteUser': 'Delete user {name}',

	'users.about.title': 'About user management',
	'users.about.admins': 'Admin users have full access to all UNOS features including user management.',
	'users.about.users': 'Regular users can access basic features but cannot manage other users.',
	'users.about.self': 'You cannot delete your own account.',
	'users.about.passwords': {
		one: 'Passwords must be at least {n} character long.',
		other: 'Passwords must be at least {n} characters long.'
	},
	'users.about.smb':
		'SMB (network file) access is off by default. Allow it per user; administrators cannot use SMB.',
	'users.about.twoFactor': '2FA protects only the web interface.',

	'users.username.hint': '1–32 lowercase letters, digits, _ or -, starting with a letter',
	'users.username.invalid': 'Use 1–32 lowercase letters, digits, _ or -, starting with a letter',
	'users.password.short': { one: 'Use at least {n} character', other: 'Use at least {n} characters' },
	'users.password.mismatch': 'Passwords do not match',
	'users.smbPasswordNeeded':
		'The user turns SMB on in their Account page with their password, or set a new password here',

	'users.new.title': 'Add user',
	'users.new.confirm': 'Confirm password',
	'users.new.smb': 'Allow SMB (network file) access',
	'users.new.smbHint': 'Uses the same password. SMB has no two-factor authentication.',
	'users.new.failed': 'User was not created',
	'users.new.submit': 'Create user',
	'users.new.done': 'User "{name}" created',

	'users.edit.title': 'Edit user {name}',
	'users.edit.titleWithoutName': 'Edit user',
	'users.edit.admin': 'Administrator',
	'users.edit.adminWarning':
		'Administrators cannot use SMB: making {name} an administrator removes SMB access and their share permissions, and ends their open SMB connections',
	'users.edit.password': 'New password',
	'users.edit.passwordHint': 'Leave empty to keep the current password',
	'users.edit.confirm': 'Confirm new password',
	'users.edit.smbPassword': "Also changes the SMB password and ends the user's open SMB connections",
	'users.edit.turnOnSmb': 'Also turn SMB on with this password',
	'users.edit.smbTitle': 'SMB access',
	'users.edit.smbUnknown': 'The SMB state of this user is not known.',
	'users.edit.smbOn': 'SMB is on: {name} signs in as {login} with their UNOS password.',
	'users.edit.smbOff': 'SMB is off for {name}.',
	'users.edit.allowSmb': 'Allow SMB access',
	'users.edit.saving': 'Saving...',
	'users.edit.failed': 'The change was not made',
	'users.edit.close': 'Close',
	'users.edit.save': 'Save',
	'users.edit.done': 'User "{name}" updated',

	// The confirmation of Turn off SMB: before and after the user's name.
	'users.smbOff.title': 'Turn off SMB',
	'users.smbOff.before': 'Turn off SMB for',
	'users.smbOff.after': '? Their open SMB connections are ended.',
	'users.smbOff.submit': 'Turn off SMB',

	// The confirmation of Delete user: before and after the user's name.
	'users.delete.title': 'Delete user',
	'users.delete.before': 'Are you sure you want to delete user',
	'users.delete.after': '? This action cannot be undone.',
	'users.delete.failed': 'User was not deleted',
	'users.delete.submit': 'Delete user',
	'users.delete.done': 'User "{name}" deleted',

	// End sessions (ADR-0329): signs the user out on every device; API tokens stay.
	'users.endSessionsOf': 'End the sessions of {name}',
	'users.endSessions.done': 'Sessions of "{name}" ended: {n}',
	'users.endSessions.failed': 'Sessions were not ended'
};

export const ru: Translation<typeof en> = {
	'users.title': 'Пользователи',
	'users.add': 'Добавить пользователя',
	'users.loadFailed': 'Не удалось загрузить пользователей',
	'users.count': 'Пользователи ({count})',
	'users.empty': 'Пользователи не найдены',
	'users.role': 'Роль',
	'users.twoFactor': '2FA',
	'users.smb': 'SMB',
	'users.created': 'Создан',
	'users.actions': 'Действия',
	'users.you': 'Вы',
	'users.admin': 'Администратор',
	'users.user': 'Пользователь',
	'users.twoFactorOn': 'Включена',
	'users.twoFactorOff': 'Выключена',
	'users.editUser': 'Изменить пользователя {name}',
	'users.deleteUser': 'Удалить пользователя {name}',

	'users.about.title': 'Об управлении пользователями',
	'users.about.admins':
		'Администраторы имеют полный доступ ко всем функциям UNOS, включая управление пользователями.',
	'users.about.users':
		'Обычным пользователям доступны основные функции, но управлять другими пользователями они не могут.',
	'users.about.self': 'Свою учётную запись удалить нельзя.',
	'users.about.passwords': {
		one: 'Пароль должен содержать не менее {n} символа.',
		few: 'Пароль должен содержать не менее {n} символов.',
		many: 'Пароль должен содержать не менее {n} символов.'
	},
	'users.about.smb':
		'Доступ по SMB (сетевой доступ к файлам) по умолчанию выключен. Разрешайте его каждому пользователю отдельно; администраторы не могут пользоваться SMB.',
	'users.about.twoFactor': '2FA защищает только веб-интерфейс.',

	'users.username.hint': '1–32 символа: строчные латинские буквы, цифры, _ или -; первый символ — буква',
	'users.username.invalid':
		'Используйте 1–32 символа: строчные латинские буквы, цифры, _ или -; первый символ — буква',
	'users.password.short': {
		one: 'Используйте не менее {n} символа',
		few: 'Используйте не менее {n} символов',
		many: 'Используйте не менее {n} символов'
	},
	'users.password.mismatch': 'Пароли не совпадают',
	'users.smbPasswordNeeded':
		'Пользователь включает SMB сам на странице «Учётная запись» своим паролем — или задайте ему новый пароль здесь',

	'users.new.title': 'Новый пользователь',
	'users.new.confirm': 'Подтверждение пароля',
	'users.new.smb': 'Разрешить доступ по SMB (сетевой доступ к файлам)',
	'users.new.smbHint': 'Используется тот же пароль. В SMB нет двухфакторной аутентификации.',
	'users.new.failed': 'Пользователь не создан',
	'users.new.submit': 'Создать пользователя',
	'users.new.done': 'Пользователь «{name}» создан',

	'users.edit.title': 'Изменение пользователя {name}',
	'users.edit.titleWithoutName': 'Изменение пользователя',
	'users.edit.admin': 'Администратор',
	'users.edit.adminWarning':
		'Администраторы не могут пользоваться SMB: назначение {name} администратором отключит доступ по SMB, удалит права на общие папки и закроет открытые подключения по SMB',
	'users.edit.password': 'Новый пароль',
	'users.edit.passwordHint': 'Оставьте пустым, чтобы сохранить текущий пароль',
	'users.edit.confirm': 'Подтверждение нового пароля',
	'users.edit.smbPassword': 'Также меняет пароль SMB и закрывает открытые подключения пользователя по SMB',
	'users.edit.turnOnSmb': 'Также включить SMB с этим паролем',
	'users.edit.smbTitle': 'Доступ по SMB',
	'users.edit.smbUnknown': 'Состояние SMB этого пользователя неизвестно.',
	'users.edit.smbOn': 'SMB включён: {name} входит как {login} со своим паролем UNOS.',
	'users.edit.smbOff': 'SMB выключен для {name}.',
	'users.edit.allowSmb': 'Разрешить доступ по SMB',
	'users.edit.saving': 'Сохранение...',
	'users.edit.failed': 'Изменение не внесено',
	'users.edit.close': 'Закрыть',
	'users.edit.save': 'Сохранить',
	'users.edit.done': 'Пользователь «{name}» изменён',

	'users.smbOff.title': 'Выключение SMB',
	'users.smbOff.before': 'Выключить SMB для',
	'users.smbOff.after': '? Открытые подключения пользователя по SMB будут закрыты.',
	'users.smbOff.submit': 'Выключить SMB',

	'users.delete.title': 'Удаление пользователя',
	'users.delete.before': 'Удалить пользователя',
	'users.delete.after': '? Это действие нельзя отменить.',
	'users.delete.failed': 'Пользователь не удалён',
	'users.delete.submit': 'Удалить пользователя',
	'users.delete.done': 'Пользователь «{name}» удалён',

	'users.endSessionsOf': 'Завершить сессии пользователя {name}',
	'users.endSessions.done': 'Сессии пользователя «{name}» завершены: {n}',
	'users.endSessions.failed': 'Сессии не завершены'
};
