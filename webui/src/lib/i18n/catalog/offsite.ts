import type { Translation } from '../translate';

// The off-device copy on System > Backup & Restore: src/routes/system/tabs/OffsiteCopy.svelte
// (ADR-0337).

export const en = {
	'offsite.title': 'Off-device copy',
	'offsite.intro':
		'Copies of the configuration and of the pools’ folders on a disk of its own or in an S3-compatible store, encrypted with a password that only this NAS keeps.',
	'offsite.notSetUp': 'No off-device copy is set up.',
	'offsite.loadFailed': 'Could not load the off-device copy',
	'offsite.refresh': 'Refresh',

	// The target.
	'offsite.form.title': 'Where the copies go',
	'offsite.form.kind': 'Kind of target',
	'offsite.form.disk': 'A disk',
	'offsite.form.s3': 'An S3-compatible store',
	'offsite.form.path': 'Folder of the copy',
	'offsite.form.pathHint':
		'A folder on a disk mounted below /mnt (such as a USB disk added as a pool), made when it is missing. Not the system disk.',
	'offsite.form.endpoint': 'Endpoint',
	'offsite.form.endpointHint': 'https:// or http:// with the host and port, such as https://s3.eu-central-1.amazonaws.com',
	'offsite.form.bucket': 'Bucket',
	'offsite.form.prefix': 'Prefix (optional)',
	'offsite.form.region': 'Region (optional)',
	'offsite.form.accessKeyId': 'Access key ID',
	'offsite.form.secretKey': 'Secret access key',
	'offsite.form.secretKept': 'Leave it empty to keep the stored key.',
	'offsite.form.limitUpload': 'Upload limit, KiB/s',
	'offsite.form.limitDownload': 'Download limit, KiB/s',
	'offsite.form.limitHint': '0: no limit.',

	// What is copied.
	'offsite.form.sources': 'Folders to copy',
	'offsite.form.sourcesHint':
		'The pools, or folders of them. Their snapshots and trash are left out, and so is the folder of the copy when it lies inside one.',
	'offsite.form.newSource': 'Another folder to copy',
	'offsite.form.addSource': 'Add folder',
	'offsite.form.removeSource': 'Remove {path}',
	'offsite.form.noSources': 'Choose at least one folder to copy.',

	// The password.
	'offsite.form.password': 'Password of the copy repository',
	'offsite.form.passwordAgain': 'Password again',
	'offsite.form.passwordWarning':
		'Without this password nobody, UNOS included, can read the copies, and it is not in the configuration backups: keep it somewhere outside this device.',
	'offsite.form.passwordTooShort': 'At least {n} characters',
	'offsite.form.passwordsDiffer': 'The passwords differ',
	'offsite.form.passwordKept': 'Leave both fields empty to keep the stored password.',
	'offsite.form.passwordNeeded': 'At least {n} characters, typed twice.',

	// The retention and the checks.
	'offsite.form.keepDaily': 'Daily snapshots kept',
	'offsite.form.keepWeekly': 'Weekly snapshots kept',
	'offsite.form.keepMonthly': 'Monthly snapshots kept',
	'offsite.form.checkPercent': 'Share of the data each check reads, %',

	'offsite.form.save': 'Save and test',
	'offsite.form.saving': 'Testing...',
	'offsite.form.saved': 'Saved: the copy repository answered.',
	'offsite.form.saveFailed': 'Could not save the settings of the copy',
	'offsite.form.forget': 'Forget this target',
	'offsite.form.confirmForget':
		'Forget the target of the copy? Its settings and password are removed from this NAS; the copies stay where they are.',
	'offsite.form.forgetFailed': 'Could not forget the target',

	// The warnings of the settings, by the kind the server names.
	'offsite.warning.sameDisk':
		'This copy is on a disk it copies: it protects against deleted and damaged files, not against the loss of that disk.',

	// The state.
	'offsite.state.title': 'State',
	'offsite.state.never': 'Last copy: never',
	'offsite.state.succeeded': 'Last copy: succeeded',
	'offsite.state.failed': 'Last copy: failed',
	'offsite.state.started': 'Started {time}',
	'offsite.state.byTask': 'by a task of the Scheduler',
	'offsite.state.byHand': 'by Run now',
	'offsite.state.files': '{new} new, {changed} changed and {unmodified} unmodified files, {added} added',
	'offsite.state.checkPassed': 'Check: passed',
	'offsite.state.checkFailed': 'Check: failed',
	'offsite.state.checkNotRun': 'Check: not run',
	'offsite.state.restoreTestPassed': 'Restore test: passed',
	'offsite.state.restoreTestFailed': 'Restore test: failed',
	'offsite.state.restoreTestNotRun': 'Restore test: not run',
	'offsite.state.failedAt': 'Failed at {step}:',
	'offsite.state.runNow': 'Run now',
	'offsite.state.runFailed': 'Could not start a copy',
	'offsite.state.runStarted': 'The copy runs in the background: Refresh shows how far it is.',
	// The steps of a copy, by the name the server gives them.
	'offsite.step.settings': 'the settings',
	'offsite.step.folders': 'the folders',
	'offsite.step.unlock': 'removing stale locks',
	'offsite.step.configuration': 'the copy of the configuration',
	'offsite.step.data': 'the copy of the folders',
	'offsite.step.forget': 'removing old snapshots',
	'offsite.step.check': 'the check',
	'offsite.step.restore_test': 'the restore test',
	// What runs, by the kind the server names.
	'offsite.running.copy': 'A copy runs, since {time}.',
	'offsite.running.restoreFiles': 'A restore into {folder} runs, since {time}.',
	'offsite.running.restoreConfiguration': 'A restore of the configuration runs, since {time}.',
	'offsite.running.settings': 'The settings are being changed.',
	'offsite.restore.succeeded': 'Last restore: succeeded, into',
	'offsite.restore.failed': 'Last restore: failed, into',

	// The scheduled task.
	'offsite.task.none': 'No scheduled task: copies run only on Run now.',
	'offsite.task.add': 'Add a daily copy at 03:00',
	'offsite.task.name': 'Off-device copy',
	'offsite.task.addFailed': 'Could not add the task',
	'offsite.task.some': 'A scheduled task copies: {names}',
	'offsite.task.next': 'Next run {time}.',

	// The snapshots and the restores.
	'offsite.snapshots.title': 'Snapshots',
	'offsite.snapshots.label': 'Snapshots of the copy',
	'offsite.snapshots.empty': 'No snapshots yet: Run now makes the first.',
	'offsite.snapshots.loadFailed': 'The snapshots could not be read',
	'offsite.snapshots.configuration': 'Configuration',
	'offsite.snapshots.data': 'Folders',
	'offsite.snapshots.other': 'Other',
	'offsite.snapshots.files': { one: '{n} file', other: '{n} files' },
	'offsite.snapshots.restoreConfiguration': 'Restore configuration',
	'offsite.snapshots.restoreConfigurationFailed': 'Could not restore the configuration',
	'offsite.snapshots.inBackupList': 'In the backup list as {name}: restore it there.',
	'offsite.snapshots.restoreFiles': 'Restore files',
	'offsite.snapshots.pool': 'Pool',
	'offsite.snapshots.folder': 'Folder in the snapshot',
	'offsite.snapshots.folderHint': 'Optional: a folder as the snapshot holds it, such as {path}. Empty: all of it.',
	'offsite.snapshots.into': 'Into a new folder restored-<date>-<time> at the top of the pool. Nothing is overwritten.',
	'offsite.snapshots.restore': 'Restore',
	'offsite.snapshots.restoreFilesFailed': 'Could not restore the files',
	'offsite.snapshots.restoring': 'The restore into {folder} runs in the background: Refresh shows when it ends.'
};

export const ru: Translation<typeof en> = {
	'offsite.title': 'Копия вне устройства',
	'offsite.intro':
		'Копии конфигурации и папок пулов на отдельном диске или в S3-совместимом хранилище, зашифрованные паролем, который хранится только на этом NAS.',
	'offsite.notSetUp': 'Копия вне устройства не настроена.',
	'offsite.loadFailed': 'Не удалось загрузить копию вне устройства',
	'offsite.refresh': 'Обновить',

	'offsite.form.title': 'Куда сохраняются копии',
	'offsite.form.kind': 'Тип хранилища',
	'offsite.form.disk': 'Диск',
	'offsite.form.s3': 'S3-совместимое хранилище',
	'offsite.form.path': 'Папка копии',
	'offsite.form.pathHint':
		'Папка на диске, смонтированном в /mnt (например, USB-диск, добавленный как пул); создаётся, если её нет. Не системный диск.',
	'offsite.form.endpoint': 'Адрес хранилища',
	'offsite.form.endpointHint': 'https:// или http:// с хостом и портом, например https://s3.eu-central-1.amazonaws.com',
	'offsite.form.bucket': 'Бакет',
	'offsite.form.prefix': 'Префикс (необязательно)',
	'offsite.form.region': 'Регион (необязательно)',
	'offsite.form.accessKeyId': 'ID ключа доступа',
	'offsite.form.secretKey': 'Секретный ключ доступа',
	'offsite.form.secretKept': 'Оставьте поле пустым, чтобы сохранить прежний ключ.',
	'offsite.form.limitUpload': 'Ограничение отправки, КиБ/с',
	'offsite.form.limitDownload': 'Ограничение загрузки, КиБ/с',
	'offsite.form.limitHint': '0 — без ограничения.',

	'offsite.form.sources': 'Папки для копирования',
	'offsite.form.sourcesHint':
		'Пулы или их папки. Их снимки и корзина не копируются, как и папка копии, если она лежит внутри одной из них.',
	'offsite.form.newSource': 'Ещё одна папка для копирования',
	'offsite.form.addSource': 'Добавить папку',
	'offsite.form.removeSource': 'Убрать {path}',
	'offsite.form.noSources': 'Выберите хотя бы одну папку для копирования.',

	'offsite.form.password': 'Пароль хранилища копий',
	'offsite.form.passwordAgain': 'Пароль ещё раз',
	'offsite.form.passwordWarning':
		'Без этого пароля никто, включая UNOS, не сможет прочитать копии, и в резервных копиях конфигурации его нет: храните его где-нибудь вне этого устройства.',
	'offsite.form.passwordTooShort': 'Не менее {n} символов',
	'offsite.form.passwordsDiffer': 'Пароли не совпадают',
	'offsite.form.passwordKept': 'Оставьте оба поля пустыми, чтобы сохранить прежний пароль.',
	'offsite.form.passwordNeeded': 'Не менее {n} символов, дважды.',

	'offsite.form.keepDaily': 'Хранить ежедневных снимков',
	'offsite.form.keepWeekly': 'Хранить еженедельных снимков',
	'offsite.form.keepMonthly': 'Хранить ежемесячных снимков',
	'offsite.form.checkPercent': 'Доля данных, читаемая при проверке, %',

	'offsite.form.save': 'Сохранить и проверить',
	'offsite.form.saving': 'Проверка...',
	'offsite.form.saved': 'Сохранено: хранилище копий ответило.',
	'offsite.form.saveFailed': 'Не удалось сохранить настройки копии',
	'offsite.form.forget': 'Забыть это хранилище',
	'offsite.form.confirmForget':
		'Забыть хранилище копий? Его настройки и пароль будут удалены с этого NAS; сами копии останутся на месте.',
	'offsite.form.forgetFailed': 'Не удалось забыть хранилище',

	'offsite.warning.sameDisk':
		'Эта копия лежит на диске, который она копирует: она защищает от удалённых и повреждённых файлов, но не от потери этого диска.',

	'offsite.state.title': 'Состояние',
	'offsite.state.never': 'Последнее копирование: не было',
	'offsite.state.succeeded': 'Последнее копирование: успешно',
	'offsite.state.failed': 'Последнее копирование: ошибка',
	'offsite.state.started': 'Начато {time}',
	'offsite.state.byTask': 'задачей планировщика',
	'offsite.state.byHand': 'кнопкой «Запустить сейчас»',
	'offsite.state.files': 'новых файлов: {new}, изменённых: {changed}, без изменений: {unmodified}; добавлено {added}',
	'offsite.state.checkPassed': 'Проверка: пройдена',
	'offsite.state.checkFailed': 'Проверка: ошибка',
	'offsite.state.checkNotRun': 'Проверка: не выполнялась',
	'offsite.state.restoreTestPassed': 'Проверка восстановления: пройдена',
	'offsite.state.restoreTestFailed': 'Проверка восстановления: ошибка',
	'offsite.state.restoreTestNotRun': 'Проверка восстановления: не выполнялась',
	'offsite.state.failedAt': 'Ошибка на шаге «{step}»:',
	'offsite.state.runNow': 'Запустить сейчас',
	'offsite.state.runFailed': 'Не удалось запустить копирование',
	'offsite.state.runStarted': 'Копирование идёт в фоне: кнопка «Обновить» покажет, как далеко оно продвинулось.',
	'offsite.step.settings': 'настройки',
	'offsite.step.folders': 'папки',
	'offsite.step.unlock': 'снятие устаревших блокировок',
	'offsite.step.configuration': 'копирование конфигурации',
	'offsite.step.data': 'копирование папок',
	'offsite.step.forget': 'удаление старых снимков',
	'offsite.step.check': 'проверка',
	'offsite.step.restore_test': 'проверка восстановления',
	'offsite.running.copy': 'Идёт копирование, с {time}.',
	'offsite.running.restoreFiles': 'Идёт восстановление в {folder}, с {time}.',
	'offsite.running.restoreConfiguration': 'Идёт восстановление конфигурации, с {time}.',
	'offsite.running.settings': 'Настройки меняются.',
	'offsite.restore.succeeded': 'Последнее восстановление: успешно, в папку',
	'offsite.restore.failed': 'Последнее восстановление: ошибка, в папку',

	'offsite.task.none': 'Задачи по расписанию нет: копии создаются только кнопкой «Запустить сейчас».',
	'offsite.task.add': 'Добавить ежедневное копирование в 03:00',
	'offsite.task.name': 'Копия вне устройства',
	'offsite.task.addFailed': 'Не удалось добавить задачу',
	'offsite.task.some': 'Копирует задача по расписанию: {names}',
	'offsite.task.next': 'Следующий запуск {time}.',

	'offsite.snapshots.title': 'Снимки',
	'offsite.snapshots.label': 'Снимки копии',
	'offsite.snapshots.empty': 'Снимков пока нет: первый создаст «Запустить сейчас».',
	'offsite.snapshots.loadFailed': 'Не удалось прочитать снимки',
	'offsite.snapshots.configuration': 'Конфигурация',
	'offsite.snapshots.data': 'Папки',
	'offsite.snapshots.other': 'Другое',
	'offsite.snapshots.files': { one: '{n} файл', few: '{n} файла', many: '{n} файлов' },
	'offsite.snapshots.restoreConfiguration': 'Восстановить конфигурацию',
	'offsite.snapshots.restoreConfigurationFailed': 'Не удалось восстановить конфигурацию',
	'offsite.snapshots.inBackupList': 'В списке резервных копий как {name}: восстановите её там.',
	'offsite.snapshots.restoreFiles': 'Восстановить файлы',
	'offsite.snapshots.pool': 'Пул',
	'offsite.snapshots.folder': 'Папка в снимке',
	'offsite.snapshots.folderHint': 'Необязательно: папка, как она записана в снимке, например {path}. Пусто — снимок целиком.',
	'offsite.snapshots.into': 'В новую папку restored-<дата>-<время> в корне пула. Ничего не перезаписывается.',
	'offsite.snapshots.restore': 'Восстановить',
	'offsite.snapshots.restoreFilesFailed': 'Не удалось восстановить файлы',
	'offsite.snapshots.restoring': 'Восстановление в {folder} идёт в фоне: кнопка «Обновить» покажет, когда оно закончится.'
};
