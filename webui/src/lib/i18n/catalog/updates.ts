import type { Translation } from '../translate';

// System > Updates: src/routes/system/tabs/UpdateTab.svelte and src/lib/components/system/updates
// (ADR-0234, ADR-0443).

export const en = {
	'updates.title': 'Updates',
	'updates.card.title': 'UNOS version',

	'updates.current.error': 'The current version could not be read',
	'updates.current.version': 'Installed',
	// The version the server answers as "unknown": UNOS was built without one.
	'updates.current.unknown': 'unknown',
	// The badge of a version that is not a release.
	'updates.current.dev': 'dev',

	'updates.latest.checkFailed': 'Could not check for updates',
	'updates.latest.none': 'No release is published yet.',
	'updates.latest.available': 'Update available: {tag}',
	'updates.latest.howTo': 'Update with the script below.',
	'updates.latest.upToDate': 'You run the latest release, {tag}.',
	'updates.latest.github': 'View on GitHub',
	'updates.latest.published': 'Published {date}',
	'updates.latest.prerelease': 'Pre-release',
	'updates.latest.checkAgain': 'Check again',
	'updates.latest.includePrereleases': 'Include pre-releases',

	'updates.how.title': 'How to update',
	'updates.how.run': 'UNOS is updated on the host, with its update script. Run it there as root:',
	'updates.how.details':
		'It backs up the database and the data volume, builds the new version while UNOS keeps running, and starts the previous version again if the new one does not answer. The web interface cannot install or roll back updates on this NAS yet.',
	'updates.how.more': 'What the script does',
	// The server's reason why the web interface cannot install releases here (English).
	'updates.how.why': 'Why not from here',

	'updates.install': 'Install {tag}',
	'updates.installTitle': 'Install UNOS {tag}?',
	'updates.installSteps.images': 'UNOS gets the images of {tag}, then backs up its database.',
	'updates.installSteps.restart': 'UNOS restarts with {tag}: the web interface is away for a few minutes.',
	'updates.installSteps.rollback':
		'If {tag} does not answer within 5 minutes, UNOS goes back to {current} with the database of the backup.',
	'updates.installConfirm': 'Install update',
	'updates.installFailed': 'The update could not be started',
	'updates.jobRuns': 'An update or a rollback runs',
	'updates.latest.installHere':
		'Install it here: UNOS backs up its database first and goes back if the new version does not answer.',
	'updates.timeZone': "Times on this tab follow the NAS's clock: {zone}.",
	'updates.latest.stableOnly': 'Install Update installs stable releases only.',

	'updates.job.title': 'Last update',
	'updates.job.running': 'Running',
	'updates.job.done': 'Done',
	'updates.job.progress': 'Update progress',
	'updates.job.checking': 'Starting',
	'updates.job.downloading': 'Getting the images of {version}',
	'updates.job.backingUp': 'Backing up the database',
	'updates.job.installing': 'Starting {version}',
	'updates.job.verifying': 'Waiting for {version} to answer',
	'updates.job.rollback': 'Rolling back',
	'updates.job.updated': 'Updated to {version}',
	'updates.job.rolledBack': 'Rolled back to {version}',
	'updates.job.updateFailed': 'The update to {version} failed',
	'updates.job.rollbackFailed': 'The rollback to {version} failed',
	'updates.job.rollbackFailedEarly': 'The rollback failed',
	'updates.job.rolledBackAfter': 'UNOS went back to the version before, with the database of its backup.',

	'updates.rollback': 'Roll back',
	'updates.rollbackTitle': 'Roll back to the version before?',
	'updates.rollbackSteps.database':
		'The database returns to the backup taken before the last update: changes made since then are lost.',
	'updates.rollbackSteps.restart': 'UNOS restarts with the version before.',
	'updates.rollbackConfirm': 'Roll back',
	'updates.rollbackFailed': 'The rollback could not be started',

	'updates.history.title': 'Update history',
	'updates.history.date': 'Started',
	'updates.history.from': 'From',
	'updates.history.to': 'To',
	'updates.history.result': 'Result',
	'updates.history.success': 'Installed',
	'updates.history.failed': 'Failed',

	'updates.auto.title': 'Automatic install',
	'updates.auto.switch': 'Install new releases automatically',
	'updates.auto.help': 'UNOS looks for a new stable release at this interval and installs it as Install does.',
	'updates.auto.interval': 'Look every',
	'updates.auto.hours': 'h',
	'updates.auto.next': 'Next look: {date}',
	'updates.auto.save': 'Save',
	'updates.auto.noChanges': 'No changes',
	'updates.auto.invalid': 'The interval is 1 to 168 hours',
	'updates.auto.saved': 'Automatic install saved',
	'updates.auto.saveFailed': 'The automatic install could not be saved',
};

export const ru: Translation<typeof en> = {
	'updates.title': 'Обновления',
	'updates.card.title': 'Версия UNOS',

	'updates.current.error': 'Не удалось прочитать текущую версию',
	'updates.current.version': 'Установлена',
	'updates.current.unknown': 'неизвестна',
	'updates.current.dev': 'разработка',

	'updates.latest.checkFailed': 'Не удалось проверить обновления',
	'updates.latest.none': 'Выпусков пока не опубликовано.',
	'updates.latest.available': 'Доступно обновление: {tag}',
	'updates.latest.howTo': 'Обновите UNOS с помощью сценария ниже.',
	'updates.latest.upToDate': 'Установлен последний выпуск, {tag}.',
	'updates.latest.github': 'Открыть на GitHub',
	'updates.latest.published': 'Опубликован {date}',
	'updates.latest.prerelease': 'Предварительный выпуск',
	'updates.latest.checkAgain': 'Проверить снова',
	'updates.latest.includePrereleases': 'Учитывать предварительные выпуски',

	'updates.how.title': 'Как обновить',
	'updates.how.run': 'UNOS обновляется на хосте своим сценарием обновления. Запустите его там от имени root:',
	'updates.how.details':
		'Сценарий создаёт резервную копию базы данных и тома данных, собирает новую версию, пока UNOS продолжает работать, и снова запускает прежнюю версию, если новая не отвечает. На этом NAS веб-интерфейс пока не устанавливает и не откатывает обновления.',
	'updates.how.more': 'Что делает сценарий',
	'updates.how.why': 'Почему не отсюда',

	'updates.install': 'Установить {tag}',
	'updates.installTitle': 'Установить UNOS {tag}?',
	'updates.installSteps.images': 'UNOS получит образы {tag}, затем создаст резервную копию своей базы данных.',
	'updates.installSteps.restart': 'UNOS перезапустится с {tag}: веб-интерфейс будет недоступен несколько минут.',
	'updates.installSteps.rollback':
		'Если {tag} не ответит за 5 минут, UNOS вернётся к {current} с базой данных из резервной копии.',
	'updates.installConfirm': 'Установить обновление',
	'updates.installFailed': 'Не удалось запустить обновление',
	'updates.jobRuns': 'Идёт обновление или откат',
	'updates.latest.installHere':
		'Установите его здесь: UNOS сначала сохранит базу данных и вернётся назад, если новая версия не ответит.',
	'updates.timeZone': 'Время на этой вкладке — по часам NAS: {zone}.',
	'updates.latest.stableOnly': 'Установка обновлений ставит только стабильные выпуски.',

	'updates.job.title': 'Последнее обновление',
	'updates.job.running': 'Выполняется',
	'updates.job.done': 'Готово',
	'updates.job.progress': 'Ход обновления',
	'updates.job.checking': 'Запуск',
	'updates.job.downloading': 'Получение образов {version}',
	'updates.job.backingUp': 'Резервное копирование базы данных',
	'updates.job.installing': 'Запуск {version}',
	'updates.job.verifying': 'Ожидание ответа {version}',
	'updates.job.rollback': 'Откат',
	'updates.job.updated': 'Обновлено до {version}',
	'updates.job.rolledBack': 'Откат к {version} выполнен',
	'updates.job.updateFailed': 'Обновление до {version} не удалось',
	'updates.job.rollbackFailed': 'Откат к {version} не удался',
	'updates.job.rollbackFailedEarly': 'Откат не удался',
	'updates.job.rolledBackAfter': 'UNOS вернулся к прежней версии с базой данных из её резервной копии.',

	'updates.rollback': 'Откатить',
	'updates.rollbackTitle': 'Вернуться к прежней версии?',
	'updates.rollbackSteps.database':
		'База данных вернётся к резервной копии, сделанной перед последним обновлением: изменения после неё будут потеряны.',
	'updates.rollbackSteps.restart': 'UNOS перезапустится с прежней версией.',
	'updates.rollbackConfirm': 'Откатить',
	'updates.rollbackFailed': 'Не удалось запустить откат',

	'updates.history.title': 'История обновлений',
	'updates.history.date': 'Начало',
	'updates.history.from': 'С версии',
	'updates.history.to': 'На версию',
	'updates.history.result': 'Результат',
	'updates.history.success': 'Установлено',
	'updates.history.failed': 'Не удалось',

	'updates.auto.title': 'Автоматическая установка',
	'updates.auto.switch': 'Устанавливать новые выпуски автоматически',
	'updates.auto.help': 'UNOS ищет новый стабильный выпуск с этим интервалом и устанавливает его, как кнопка установки.',
	'updates.auto.interval': 'Проверять каждые',
	'updates.auto.hours': 'ч',
	'updates.auto.next': 'Следующая проверка: {date}',
	'updates.auto.save': 'Сохранить',
	'updates.auto.noChanges': 'Нет изменений',
	'updates.auto.invalid': 'Интервал — от 1 до 168 часов',
	'updates.auto.saved': 'Автоматическая установка сохранена',
	'updates.auto.saveFailed': 'Не удалось сохранить автоматическую установку',
};
