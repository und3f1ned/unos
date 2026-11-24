import type { Translation } from '../translate';

// The update of an installed app to the catalog's newer images, its rollback and the earlier
// version that an update keeps, on the App Store's Installed tab and its job cards
// (src/lib/components/apps/AppUpdateDialog.svelte, AppInstallJobs.svelte, ADR-0408).

export const en = {
	'appUpdates.available': 'Update to {version}',
	'appUpdates.title': 'Update {name}',
	'appUpdates.loadFailed': 'The update cannot run: {error}',
	'appUpdates.versions': 'From {from} to {to}',
	'appUpdates.versionUnknown': 'the installed version',
	'appUpdates.containers': 'Containers',
	'appUpdates.changedCount': { one: '{n} of {total} gets a new image', other: '{n} of {total} get a new image' },
	'appUpdates.newImage': 'New image',
	'appUpdates.sameImage': 'Same image',
	'appUpdates.added': 'Added',
	'appUpdates.leftOut': 'Kept with the earlier version',
	'appUpdates.download': 'Download',
	// Where the pulls write: the pool that holds Docker's images, or the system disk (ADR-0493).
	'appUpdates.imagesPool': 'Pool {pool}',
	'appUpdates.systemDisk': 'System disk',
	'appUpdates.writes': 'About {writes}, of {free} free',
	'appUpdates.writesNoFree': 'About {writes}',
	'appUpdates.copies': 'Copy of the database',
	'appUpdates.copy':
		'Before the new version starts, {folder} ({size}) is copied to {copy} on the pool "{pool}", which has {free} free.',
	'appUpdates.kept':
		'Kept: the data folders, the password files, the settings, the ports, the limits and the SMB group option. The earlier containers stay, stopped, until you roll back or remove them.',
	'appUpdates.failureNote':
		'If the new version does not start, UNOS returns the app to its earlier version and its database to the copy; if the copy cannot go back in place, the earlier version stays stopped, ready for a rollback.',
	'appUpdates.details': "What stays, and each container's image",
	'appUpdates.start': 'Update now',
	'appUpdates.startFailed': 'The update could not be started: {error}',
	'appUpdates.earlierTitle': 'Earlier version kept',
	'appUpdates.earlier':
		'The update kept the earlier version ({containers}), stopped, and the copy of its database ({copies}). Its containers do not start on their own, nor from the Containers page.',
	'appUpdates.earlierNoCopy':
		'The update kept the earlier version ({containers}), stopped. Its containers do not start on their own, nor from the Containers page.',
	'appUpdates.earlierVersion': 'Earlier version: {version}.',
	'appUpdates.rollback': 'Roll back',
	'appUpdates.removeEarlier': 'Remove earlier version',
	'appUpdates.rollbackTitle': 'Roll {name} back to its earlier version?',
	'appUpdates.confirmRollback':
		'Its database returns to the copy made before the update: what {name} stored since then is lost.',
	'appUpdates.rollbackAction': 'Roll back',
	'appUpdates.removeTitle': 'Remove the earlier version of {name}?',
	'appUpdates.confirmRemove': 'The copy of its database goes with it, and a rollback is then no longer possible.',
	'appUpdates.removeAction': 'Remove earlier version',
	'appUpdates.rollbackFailed': 'The rollback could not be started: {error}',
	'appUpdates.removeFailed': 'The earlier version could not be removed: {error}',
	'appUpdates.job.update': 'Update of {name}',
	'appUpdates.job.rollback': 'Rollback of {name}',
	'appUpdates.state.update': 'Updating',
	'appUpdates.state.rollback': 'Rolling back',
	'appUpdates.state.updated': 'Updated',
	'appUpdates.state.rolledBack': 'Rolled back',
	'appUpdates.step.checking': 'Checking the images, the free space and the database',
	'appUpdates.step.stopping': 'Stopping the app',
	'appUpdates.step.copying': 'Copying the database',
	'appUpdates.step.restoring': 'Returning to the earlier version',
	'appUpdates.updatedNotice':
		'{name} was updated. Its earlier version and the copy of its database are kept until you roll back or remove them.',
	'appUpdates.rolledBackNotice': '{name} was returned to its earlier version.',
	'appUpdates.updateFailed': 'Update failed',
	'appUpdates.rollbackFailedTitle': 'Rollback failed',
	'appUpdates.cancelUpdate': 'Cancel update',
	'appUpdates.cancelTitle': 'Cancel the update of {name}?',
	'appUpdates.confirmCancel': 'Once the app was stopped, UNOS returns it to its earlier version.',
	'appUpdates.cancelAction': 'Cancel update',
	'appUpdates.keepGoing': 'Keep updating',
	'appUpdates.cancelledTitle': 'The update was cancelled'
};

export const ru: Translation<typeof en> = {
	'appUpdates.available': 'Обновить до {version}',
	'appUpdates.title': 'Обновление {name}',
	'appUpdates.loadFailed': 'Обновление невозможно: {error}',
	'appUpdates.versions': 'С {from} на {to}',
	'appUpdates.versionUnknown': 'установленной версии',
	'appUpdates.containers': 'Контейнеры',
	'appUpdates.changedCount': {
		one: 'Новый образ у {n} из {total}',
		few: 'Новый образ у {n} из {total}',
		many: 'Новый образ у {n} из {total}'
	},
	'appUpdates.newImage': 'Новый образ',
	'appUpdates.sameImage': 'Образ тот же',
	'appUpdates.added': 'Добавляется',
	'appUpdates.leftOut': 'Остаётся с прежней версией',
	'appUpdates.download': 'Скачивание',
	'appUpdates.imagesPool': 'Пул {pool}',
	'appUpdates.systemDisk': 'Системный диск',
	'appUpdates.writes': 'Около {writes} из свободных {free}',
	'appUpdates.writesNoFree': 'Около {writes}',
	'appUpdates.copies': 'Копия базы данных',
	'appUpdates.copy':
		'Перед запуском новой версии папка {folder} ({size}) копируется в {copy} в пуле «{pool}», где свободно {free}.',
	'appUpdates.kept':
		'Сохраняются папки с данными, файлы паролей, настройки, порты, ограничения и параметр «Запускать в группе unos-smb». Прежние контейнеры остаются остановленными, пока вы не откатите обновление или не удалите их.',
	'appUpdates.failureNote':
		'Если новая версия не запустится, UNOS вернёт приложение к прежней версии, а базу данных — к копии; если копию не удастся вернуть на место, прежняя версия останется остановленной и готовой к откату.',
	'appUpdates.details': 'Что сохраняется и образ каждого контейнера',
	'appUpdates.start': 'Обновить сейчас',
	'appUpdates.startFailed': 'Не удалось начать обновление: {error}',
	'appUpdates.earlierTitle': 'Прежняя версия сохранена',
	'appUpdates.earlier':
		'Обновление сохранило прежнюю версию ({containers}) остановленной, а также копию её базы данных ({copies}). Её контейнеры не запускаются ни сами, ни со страницы «Контейнеры».',
	'appUpdates.earlierNoCopy':
		'Обновление сохранило прежнюю версию ({containers}) остановленной. Её контейнеры не запускаются ни сами, ни со страницы «Контейнеры».',
	'appUpdates.earlierVersion': 'Прежняя версия: {version}.',
	'appUpdates.rollback': 'Откатить',
	'appUpdates.removeEarlier': 'Удалить прежнюю версию',
	'appUpdates.rollbackTitle': 'Вернуть {name} к прежней версии?',
	'appUpdates.confirmRollback':
		'База данных вернётся к копии, сделанной перед обновлением: всё, что приложение {name} сохранило после него, будет потеряно.',
	'appUpdates.rollbackAction': 'Откатить',
	'appUpdates.removeTitle': 'Удалить прежнюю версию {name}?',
	'appUpdates.confirmRemove': 'Вместе с ней удаляется копия базы данных, и откат станет невозможен.',
	'appUpdates.removeAction': 'Удалить прежнюю версию',
	'appUpdates.rollbackFailed': 'Не удалось начать откат: {error}',
	'appUpdates.removeFailed': 'Не удалось удалить прежнюю версию: {error}',
	'appUpdates.job.update': 'Обновление {name}',
	'appUpdates.job.rollback': 'Откат {name}',
	'appUpdates.state.update': 'Обновляется',
	'appUpdates.state.rollback': 'Откатывается',
	'appUpdates.state.updated': 'Обновлено',
	'appUpdates.state.rolledBack': 'Откачено',
	'appUpdates.step.checking': 'Проверка образов, свободного места и базы данных',
	'appUpdates.step.stopping': 'Остановка приложения',
	'appUpdates.step.copying': 'Копирование базы данных',
	'appUpdates.step.restoring': 'Возврат к прежней версии',
	'appUpdates.updatedNotice':
		'Приложение {name} обновлено. Прежняя версия и копия базы данных хранятся, пока вы не откатите обновление или не удалите их.',
	'appUpdates.rolledBackNotice': 'Приложение {name} возвращено к прежней версии.',
	'appUpdates.updateFailed': 'Не удалось обновить',
	'appUpdates.rollbackFailedTitle': 'Не удалось откатить',
	'appUpdates.cancelUpdate': 'Отменить обновление',
	'appUpdates.cancelTitle': 'Отменить обновление {name}?',
	'appUpdates.confirmCancel': 'Если приложение уже остановлено, UNOS вернёт его к прежней версии.',
	'appUpdates.cancelAction': 'Отменить обновление',
	'appUpdates.keepGoing': 'Продолжить обновление',
	'appUpdates.cancelledTitle': 'Обновление отменено'
};
