import type { Translation } from '../translate';

// The Users page: src/routes/users/+page.svelte. The server's messages (a refusal, the reason SMB
// is not available, the warnings of a change) stay in English (ADR-0287). A sentence with a
// user's name in bold is translated in parts around the name (ADR-0296).

export const en = {
	'users.title': 'Users',
	'users.add': 'Add user',
	'users.loadFailed': 'The users could not be loaded',
	'users.count': 'Users ({count})',
	'users.role': 'Role',
	'users.twoFactor': '2FA',
	'users.smb': 'SMB',
	'users.created': 'Created',
	'users.actions': 'Actions',
	'users.you': 'You',
	'users.admin': 'Admin',
	'users.user': 'User',
	'users.twoFactorOn': '2FA on',
	'users.twoFactorOff': '2FA off',
	'users.editUser': 'Edit user {name}',
	'users.deleteUser': 'Delete user {name}',
	// The hover texts of a row's icon buttons.
	'users.editShort': 'Edit',
	'users.endSessionsShort': 'End all sessions',
	'users.deleteShort': 'Delete',


	'users.username.hint': '1–32 lowercase letters, digits, _ or -, starting with a letter',
	'users.username.invalid': 'Use 1–32 lowercase letters, digits, _ or -, starting with a letter',
	'users.username.required': 'Enter a username',
	'users.password.short': { one: 'Use at least {n} character', other: 'Use at least {n} characters' },
	'users.password.mismatch': 'Passwords do not match',
	'users.password.required': 'Enter a password',
	'users.password.confirmRequired': 'Enter the password again',
	// Under a new password: the rule of supervisor/src/security/validators.py.
	'users.password.rule': { one: 'At least {n} character', other: 'At least {n} characters' },
	'users.smbPasswordNeeded':
		'The user turns SMB on in their Account page with their password, or set a new password here',

	'users.new.title': 'Add user',
	'users.new.confirm': 'Confirm password',
	'users.new.smb': 'Allow SMB (network file) access',
	'users.new.smbHint': 'Uses the same password. SMB has no two-factor authentication.',
	// Where the role is chosen: a new user is a regular one (UserCreate has no role).
	'users.new.roleHint': 'New users are regular users: make one an administrator with Edit.',
	'users.new.failed': 'User was not created',
	'users.new.submit': 'Create user',
	'users.new.done': 'User "{name}" created',

	'users.edit.title': 'Edit user {name}',
	'users.edit.titleWithoutName': 'Edit user',
	'users.edit.admin': 'Administrator',
	'users.edit.adminWarning':
		'Administrators cannot use SMB: making {name} an administrator removes SMB access, their share permissions and their groups, and ends their open SMB connections',
	'users.edit.password': 'New password',
	'users.edit.passwordHint': {
		one: 'At least {n} character; leave empty to keep the current password',
		other: 'At least {n} characters; leave empty to keep the current password'
	},
	'users.edit.ownPassword': 'Your own password is changed on your Account page, with the current one:',
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
	'users.edit.noChanges': 'No changes to save',
	'users.edit.done': 'User "{name}" updated',
	// A rename (ADR-0368).
	'users.edit.nameTitle': 'Name',
	'users.edit.renameHint': 'The user signs in with the new name; their sessions end',
	'users.edit.renameSelf': 'You are renaming yourself: sign in again with the new name',
	'users.edit.renameSmb':
		'SMB is on for {name}: a rename needs a new password, which becomes the UNOS and SMB password of the new name',
	'users.edit.renameAndRole': 'Change the name and the role one at a time',
	'users.edit.renamed': 'User "{old}" renamed to "{name}"',

	// The confirmation of Turn off SMB: before and after the user's name.
	'users.smbOff.title': 'Turn off SMB',
	'users.smbOff.before': 'Turn off SMB for',
	'users.smbOff.after': '? Their open SMB connections are ended.',
	'users.smbOff.submit': 'Turn off SMB',

	// The confirmation of Delete user: before and after the user's name.
	'users.delete.title': 'Delete user',
	'users.delete.before': 'Delete user',
	'users.delete.after': '? This action cannot be undone.',
	// What a deletion does (delete_user of supervisor/src/auth/router.py), one line each.
	'users.delete.sessions': 'They are signed out everywhere; their API tokens and security keys are deleted.',
	'users.delete.access': 'Their access to shares and their group memberships are removed.',
	'users.delete.smb': 'Their SMB account is removed, and its open connections end.',
	'users.delete.files': 'The files they made in the shares are kept.',
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
	'users.role': 'Роль',
	'users.twoFactor': '2FA',
	'users.smb': 'SMB',
	'users.created': 'Создан',
	'users.actions': 'Действия',
	'users.you': 'Вы',
	'users.admin': 'Администратор',
	'users.user': 'Пользователь',
	'users.twoFactorOn': '2FA включена',
	'users.twoFactorOff': '2FA выключена',
	'users.editUser': 'Изменить пользователя {name}',
	'users.deleteUser': 'Удалить пользователя {name}',
	'users.editShort': 'Изменить',
	'users.endSessionsShort': 'Завершить все сеансы',
	'users.deleteShort': 'Удалить',


	'users.username.hint': '1–32 символа: строчные латинские буквы, цифры, _ или -; первый символ — буква',
	'users.username.invalid':
		'Используйте 1–32 символа: строчные латинские буквы, цифры, _ или -; первый символ — буква',
	'users.username.required': 'Введите имя пользователя',
	'users.password.short': {
		one: 'Используйте не менее {n} символа',
		few: 'Используйте не менее {n} символов',
		many: 'Используйте не менее {n} символов'
	},
	'users.password.mismatch': 'Пароли не совпадают',
	'users.password.required': 'Введите пароль',
	'users.password.confirmRequired': 'Введите пароль ещё раз',
	'users.password.rule': { one: 'Не короче {n} символа', few: 'Не короче {n} символов', many: 'Не короче {n} символов' },
	'users.smbPasswordNeeded':
		'Пользователь включает SMB сам на странице «Учётная запись» своим паролем — или задайте ему новый пароль здесь',

	'users.new.title': 'Новый пользователь',
	'users.new.confirm': 'Подтверждение пароля',
	'users.new.smb': 'Разрешить доступ по SMB (сетевой доступ к файлам)',
	'users.new.smbHint': 'Используется тот же пароль. В SMB нет двухфакторной аутентификации.',
	'users.new.roleHint': 'Новые пользователи обычные: администратором делает кнопка «Изменить».',
	'users.new.failed': 'Пользователь не создан',
	'users.new.submit': 'Создать пользователя',
	'users.new.done': 'Пользователь «{name}» создан',

	'users.edit.title': 'Изменение пользователя {name}',
	'users.edit.titleWithoutName': 'Изменение пользователя',
	'users.edit.admin': 'Администратор',
	'users.edit.adminWarning':
		'Администраторы не могут пользоваться SMB: назначение {name} администратором отключит доступ по SMB, удалит права на общие папки и членство в группах и закроет открытые подключения по SMB',
	'users.edit.password': 'Новый пароль',
	'users.edit.passwordHint': {
		one: 'Не короче {n} символа; оставьте пустым, чтобы сохранить текущий пароль',
		few: 'Не короче {n} символов; оставьте пустым, чтобы сохранить текущий пароль',
		many: 'Не короче {n} символов; оставьте пустым, чтобы сохранить текущий пароль'
	},
	'users.edit.ownPassword': 'Свой пароль меняют на странице учётной записи, указав текущий:',
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
	'users.edit.noChanges': 'Нет изменений',
	'users.edit.done': 'Пользователь «{name}» изменён',
	'users.edit.nameTitle': 'Имя',
	'users.edit.renameHint': 'Пользователь входит под новым именем; его сеансы завершаются',
	'users.edit.renameSelf': 'Вы переименовываете себя: войдите снова под новым именем',
	'users.edit.renameSmb':
		'SMB включён для {name}: для переименования нужен новый пароль, он станет паролем UNOS и SMB под новым именем',
	'users.edit.renameAndRole': 'Измените имя и роль по отдельности',
	'users.edit.renamed': 'Пользователь «{old}» переименован в «{name}»',

	'users.smbOff.title': 'Выключение SMB',
	'users.smbOff.before': 'Выключить SMB для',
	'users.smbOff.after': '? Открытые подключения пользователя по SMB будут закрыты.',
	'users.smbOff.submit': 'Выключить SMB',

	'users.delete.title': 'Удаление пользователя',
	'users.delete.before': 'Удалить пользователя',
	'users.delete.after': '? Это действие нельзя отменить.',
	'users.delete.sessions': 'Все его сеансы завершаются; его API-токены и ключи безопасности удаляются.',
	'users.delete.access': 'Его доступ к общим папкам и участие в группах удаляются.',
	'users.delete.smb': 'Его учётная запись SMB удаляется, а её открытые подключения разрываются.',
	'users.delete.files': 'Файлы, которые он создал в общих папках, сохраняются.',
	'users.delete.failed': 'Пользователь не удалён',
	'users.delete.submit': 'Удалить пользователя',
	'users.delete.done': 'Пользователь «{name}» удалён',

	'users.endSessionsOf': 'Завершить сессии пользователя {name}',
	'users.endSessions.done': 'Сессии пользователя «{name}» завершены: {n}',
	'users.endSessions.failed': 'Сессии не завершены'
};
