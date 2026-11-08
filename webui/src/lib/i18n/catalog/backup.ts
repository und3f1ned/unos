import type { Translation } from '../translate';

// System > Backup & Restore: src/routes/system/tabs/BackupTab.svelte.

export const en = {
	'backup.title': 'Backup and Restore',
	'backup.upload': 'Upload Backup',
	'backup.create': 'Create Backup',
	'backup.empty': 'No backups yet',
	'backup.emptyHint': 'Create a backup to save your UNOS configuration',
	// A backup in the list: the version of its format (its manifest.json), which the server answers
	// as "unknown" for an encrypted backup; and whether it is encrypted.
	'backup.version': 'v{version}',
	'backup.versionUnknown': 'version unknown',
	'backup.encrypted': 'encrypted',
	'backup.restore': 'Restore',
	'backup.restoreOne': 'Restore {name}',
	'backup.download': 'Download',
	'backup.downloadOne': 'Download {name}',
	'backup.delete': 'Delete',
	'backup.deleteOne': 'Delete {name}',
	'backup.confirmDelete': 'Delete backup {name}?',

	// The titles of the errors above the list; the server's message follows.
	'backup.loadFailed': 'Could not load the backups',
	'backup.createFailed': 'Could not create a backup',
	'backup.deleteFailed': 'Could not delete {name}',
	'backup.downloadFailed': 'Could not download {name}',
	'backup.uploadFailed': 'Could not upload {name}',

	'backup.dialog.label': 'Restore backup',
	'backup.dialog.title': 'Restore Backup',
	'backup.dialog.from': 'This will restore configuration from',
	'backup.dialog.warning': 'Warning: This will overwrite current settings including:',
	'backup.dialog.database':
		'- Database (user accounts with their passwords, two-factor authentication, API tokens and the login history)',
	'backup.dialog.shares': '- SMB share configuration',
	'backup.dialog.passphrase': 'Passphrase of the encrypted backup',
	'backup.dialog.confirm': 'Type the backup filename to confirm:',
	'backup.dialog.restoring': 'Restoring...',
	'backup.dialog.complete': 'Restore Complete',
	'backup.dialog.restored': 'Restored items:',
	// The items of a restore, as the server names them.
	'backup.dialog.items.database': 'database',
	'backup.dialog.items.smbConfig': 'smb_config',
	'backup.dialog.failed': 'Restore Failed',
	'backup.dialog.failedFallback': 'Failed to restore backup',
	'backup.dialog.close': 'Close'
};

export const ru: Translation<typeof en> = {
	'backup.title': 'Резервное копирование и восстановление',
	'backup.upload': 'Загрузить резервную копию',
	'backup.create': 'Создать резервную копию',
	'backup.empty': 'Резервных копий пока нет',
	'backup.emptyHint': 'Создайте резервную копию, чтобы сохранить конфигурацию UNOS',
	'backup.version': 'версия {version}',
	'backup.versionUnknown': 'версия неизвестна',
	'backup.encrypted': 'зашифрована',
	'backup.restore': 'Восстановить',
	'backup.restoreOne': 'Восстановить {name}',
	'backup.download': 'Скачать',
	'backup.downloadOne': 'Скачать {name}',
	'backup.delete': 'Удалить',
	'backup.deleteOne': 'Удалить {name}',
	'backup.confirmDelete': 'Удалить резервную копию {name}?',

	'backup.loadFailed': 'Не удалось загрузить список резервных копий',
	'backup.createFailed': 'Не удалось создать резервную копию',
	'backup.deleteFailed': 'Не удалось удалить {name}',
	'backup.downloadFailed': 'Не удалось скачать {name}',
	'backup.uploadFailed': 'Не удалось загрузить {name}',

	'backup.dialog.label': 'Восстановление из резервной копии',
	'backup.dialog.title': 'Восстановление из резервной копии',
	'backup.dialog.from': 'Конфигурация будет восстановлена из',
	'backup.dialog.warning': 'Внимание: текущие настройки будут перезаписаны, в том числе:',
	'backup.dialog.database':
		'- База данных (учётные записи пользователей с паролями, двухфакторная аутентификация, API-токены и история входов)',
	'backup.dialog.shares': '- Настройки общих папок SMB',
	'backup.dialog.passphrase': 'Парольная фраза зашифрованной резервной копии',
	'backup.dialog.confirm': 'Для подтверждения введите имя файла резервной копии:',
	'backup.dialog.restoring': 'Восстановление...',
	'backup.dialog.complete': 'Восстановление завершено',
	'backup.dialog.restored': 'Восстановлено:',
	'backup.dialog.items.database': 'база данных',
	'backup.dialog.items.smbConfig': 'настройки SMB',
	'backup.dialog.failed': 'Восстановление не удалось',
	'backup.dialog.failedFallback': 'Не удалось восстановить резервную копию',
	'backup.dialog.close': 'Закрыть'
};
