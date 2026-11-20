import type { Translation } from '../translate';

// System > Updates: src/routes/system/tabs/UpdateTab.svelte (ADR-0234).

export const en = {
	'updates.title': 'Software Updates',

	'updates.current.title': 'Current version',
	'updates.current.error': 'The current version could not be read',
	'updates.current.version': 'Version:',
	// The version the server answers as "unknown": UNOS was built without one.
	'updates.current.unknown': 'unknown',
	// The badge of a version that is not a release.
	'updates.current.dev': 'dev',

	'updates.latest.title': 'Latest release',
	'updates.latest.checking': 'Checking GitHub for releases...',
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
		'It backs up the database and the data volume, builds the new version while UNOS keeps running, and starts the previous version again if the new one does not answer. The web interface cannot install or roll back updates yet.'
};

export const ru: Translation<typeof en> = {
	'updates.title': 'Обновления ПО',

	'updates.current.title': 'Текущая версия',
	'updates.current.error': 'Не удалось прочитать текущую версию',
	'updates.current.version': 'Версия:',
	'updates.current.unknown': 'неизвестна',
	'updates.current.dev': 'разработка',

	'updates.latest.title': 'Последний выпуск',
	'updates.latest.checking': 'Поиск выпусков на GitHub...',
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
		'Сценарий создаёт резервную копию базы данных и тома данных, собирает новую версию, пока UNOS продолжает работать, и снова запускает прежнюю версию, если новая не отвечает. Веб-интерфейс пока не умеет устанавливать и откатывать обновления.'
};
