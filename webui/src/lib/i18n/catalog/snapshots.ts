import type { Translation } from '../translate';

// The Snapshots page: src/routes/snapshots/+page.svelte. The server's messages stay in English
// (ADR-0287). A sentence with a name, a path or a link inside is translated in parts around them,
// and names on the disk (.snapshots, snap_, restore_, auto_) are constants of the page, in no text
// here; the <date>_<time> that follows a prefix in a name's pattern is words (ADR-0296).

export const en = {
	'snapshots.title': 'Snapshots',
	'snapshots.create': 'Create Snapshot',
	'snapshots.loadPoolsError': 'Could not load the pools and volumes',
	'snapshots.picker': 'Pool or volume',
	'snapshots.pool': '{pool} (Pool)',
	'snapshots.volume': '{pool}/{volume} (Volume)',
	'snapshots.volumesError': 'The volumes of {pool} could not be read',
	'snapshots.volumesErrorHint': 'The pool itself is offered; its volumes are missing from the list.',
	// Before a link to the Storage page.
	'snapshots.noPool': 'No pool is mounted. Mount a pool on the',
	'snapshots.storagePage': 'Storage page',
	'snapshots.restored': 'Snapshot restored',
	// Before the path of the copy.
	'snapshots.restoredAt': 'The copy is at',
	'snapshots.of': 'Snapshots of {name}',
	'snapshots.loading': 'Loading snapshots...',
	'snapshots.loadError': 'Could not load the snapshots',
	'snapshots.empty': 'No snapshots yet',
	'snapshots.emptyHint': 'A snapshot keeps the files as they are now, read-only.',
	'snapshots.list': 'Snapshots',
	'snapshots.name': 'Name',
	'snapshots.created': 'Created',
	'snapshots.actions': 'Actions',
	'snapshots.restoreOne': 'Restore snapshot {name}',
	'snapshots.deleteOne': 'Delete snapshot {name}',
	// After the prefix of a name that UNOS gives, in the pattern of the names of scheduled
	// snapshots (auto_) and of restored copies (restore_).
	'snapshots.dateTime': '<date>_<time>',

	// About Snapshots: before the .snapshots folder, after it, ..., before auto_<date>_<time>;
	// then before the prefixes snap_, restore_ and auto_, between the last two, and after them.
	'snapshots.about.title': 'About Snapshots',
	'snapshots.about.kept':
		'A snapshot is a read-only copy of a pool or a volume at one point in time, kept in its',
	'snapshots.about.folder': 'folder.',
	'snapshots.about.btrfs': 'btrfs makes it at once and then stores only what changes.',
	'snapshots.about.volumes':
		'A snapshot of a pool does not include the files in its volumes: take snapshots of each volume as well.',
	'snapshots.about.restore': 'Restoring makes a writable copy next to the original and overwrites nothing.',
	'snapshots.about.scheduled': 'Scheduled snapshots (System, Scheduler) are made of pools only and are named',
	'snapshots.about.names': 'The date and time in the names that UNOS gives,',
	'snapshots.about.and': 'and',
	'snapshots.about.utc': ", are in UTC; Created shows the time in your browser's time zone.",

	// Create Snapshot: before the pool's or the volume's path, and before its .snapshots folder.
	'snapshots.new.title': 'Create Snapshot',
	'snapshots.new.of': 'A read-only snapshot of',
	'snapshots.new.keptIn': ', kept in',
	'snapshots.new.pool':
		"It does not include the files in the pool's volumes: take snapshots of each volume as well. Scheduled snapshot tasks take pools only.",
	'snapshots.new.name': 'Snapshot name (optional)',
	'snapshots.new.namePlaceholder': 'Made from the date and time in UTC when empty',
	'snapshots.new.failed': 'Snapshot was not created',
	'snapshots.new.submit': 'Create',

	// Restore Snapshot: before the snapshot's name, after it, after restore_<date>_<time>, and
	// after the path the copy is made in.
	'snapshots.restore.title': 'Restore Snapshot',
	'snapshots.restore.before': 'Restore snapshot',
	'snapshots.restore.copy': '? A writable copy of it,',
	'snapshots.restore.madeIn': 'with the date and time in UTC, is made in',
	'snapshots.restore.after': '. Nothing is overwritten.',
	'snapshots.restore.poolCopy':
		"The copy is a volume of the pool: delete it with the pool's Volumes on the Storage page when it is no longer needed.",
	'snapshots.restore.volumeCopy':
		'The copy is a subvolume inside the volume: it cannot be deleted in the web interface yet, and the volume cannot be deleted while it holds the copy.',
	'snapshots.restore.failed': 'Snapshot was not restored',
	'snapshots.restore.submit': 'Restore',

	// Delete Snapshot: before the snapshot's name, and after its path in parentheses.
	'snapshots.delete.title': 'Delete Snapshot',
	'snapshots.delete.before': 'Delete snapshot',
	'snapshots.delete.after': '? This cannot be undone.',
	'snapshots.delete.failed': 'Snapshot was not deleted',
	'snapshots.delete.submit': 'Delete Snapshot'
};

export const ru: Translation<typeof en> = {
	'snapshots.title': 'Снимки',
	'snapshots.create': 'Создать снимок',
	'snapshots.loadPoolsError': 'Не удалось загрузить пулы и тома',
	'snapshots.picker': 'Пул или том',
	'snapshots.pool': '{pool} (пул)',
	'snapshots.volume': '{pool}/{volume} (том)',
	'snapshots.volumesError': 'Не удалось прочитать тома пула {pool}',
	'snapshots.volumesErrorHint': 'Сам пул можно выбрать, но его томов нет в списке.',
	'snapshots.noPool': 'Нет смонтированных пулов. Смонтируйте пул на странице',
	'snapshots.storagePage': '«Хранилище»',
	'snapshots.restored': 'Снимок восстановлен',
	'snapshots.restoredAt': 'Путь к копии:',
	'snapshots.of': 'Снимки {name}',
	'snapshots.loading': 'Загрузка снимков...',
	'snapshots.loadError': 'Не удалось загрузить снимки',
	'snapshots.empty': 'Снимков пока нет',
	'snapshots.emptyHint': 'Снимок сохраняет файлы в их нынешнем виде, только для чтения.',
	'snapshots.list': 'Снимки',
	'snapshots.name': 'Имя',
	'snapshots.created': 'Создан',
	'snapshots.actions': 'Действия',
	'snapshots.restoreOne': 'Восстановить снимок {name}',
	'snapshots.deleteOne': 'Удалить снимок {name}',
	'snapshots.dateTime': '<дата>_<время>',

	'snapshots.about.title': 'О снимках',
	'snapshots.about.kept':
		'Снимок — это доступная только для чтения копия пула или тома на определённый момент времени. Он хранится в папке',
	'snapshots.about.folder': 'этого пула или тома.',
	'snapshots.about.btrfs': 'btrfs создаёт снимок мгновенно, а затем хранит только изменения.',
	'snapshots.about.volumes': 'Снимок пула не включает файлы его томов: делайте снимки и каждого тома.',
	'snapshots.about.restore':
		'Восстановление создаёт рядом с оригиналом копию, доступную для записи, и ничего не перезаписывает.',
	'snapshots.about.scheduled':
		'Снимки по расписанию (Система, Планировщик) делаются только для пулов и называются',
	'snapshots.about.names': 'В именах, которые даёт UNOS,',
	'snapshots.about.and': 'и',
	'snapshots.about.utc':
		', дата и время указаны в UTC; в столбце «Создан» время показано в часовом поясе вашего браузера.',

	'snapshots.new.title': 'Создание снимка',
	'snapshots.new.of': 'Снимок содержимого',
	'snapshots.new.keptIn': ', доступный только для чтения, будет храниться в',
	'snapshots.new.pool':
		'В него не войдут файлы томов пула: делайте снимки и каждого тома. Задачи по расписанию делают снимки только пулов.',
	'snapshots.new.name': 'Имя снимка (необязательно)',
	'snapshots.new.namePlaceholder': 'Если пусто, имя — дата и время в UTC',
	'snapshots.new.failed': 'Снимок не создан',
	'snapshots.new.submit': 'Создать',

	'snapshots.restore.title': 'Восстановление снимка',
	'snapshots.restore.before': 'Восстановить снимок',
	'snapshots.restore.copy': '? Копия, доступная для записи, будет создана с именем',
	'snapshots.restore.madeIn': '(дата и время в UTC) в папке',
	'snapshots.restore.after': '. Ничего не перезаписывается.',
	'snapshots.restore.poolCopy':
		'Копия станет томом пула: когда она больше не нужна, удалите её в списке томов пула на странице «Хранилище».',
	'snapshots.restore.volumeCopy':
		'Копия станет подтомом внутри тома: удалить её в веб-интерфейсе пока нельзя, а сам том нельзя удалить, пока в нём есть копия.',
	'snapshots.restore.failed': 'Снимок не восстановлен',
	'snapshots.restore.submit': 'Восстановить',

	'snapshots.delete.title': 'Удаление снимка',
	'snapshots.delete.before': 'Удалить снимок',
	'snapshots.delete.after': '? Это действие нельзя отменить.',
	'snapshots.delete.failed': 'Снимок не удалён',
	'snapshots.delete.submit': 'Удалить снимок'
};
