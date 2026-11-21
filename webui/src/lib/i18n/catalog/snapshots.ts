import type { Translation } from '../translate';

// The Snapshots page: src/routes/snapshots/+page.svelte. The server's messages stay in English
// (ADR-0287). A sentence with a name, a path or a link inside is translated in parts around them,
// and names on the disk (.snapshots, snap_, restore_, auto_) are constants of the page, in no text
// here; the <date>_<time> that follows a prefix in a name's pattern is words (ADR-0296).

export const en = {
	'snapshots.title': 'Snapshots',
	'snapshots.subtitle':
		"Read-only copies of a pool or a volume. A pool's snapshot leaves out its volumes: take theirs as well.",
	'snapshots.create': 'Create snapshot',
	'snapshots.createBlocked': 'Mount a pool on the Storage page first',
	'snapshots.loadPoolsError': 'Could not load the pools and volumes',
	'snapshots.picker': 'Pool or volume',
	'snapshots.pool': '{pool} (Pool)',
	'snapshots.volume': '{pool}/{volume} (Volume)',
	'snapshots.volumesError': 'The volumes of {pool} could not be read',
	'snapshots.volumesErrorHint': 'The pool itself is offered; its volumes are missing from the list.',
	// Before a link to the Storage page.
	'snapshots.noPool': 'Snapshots are taken of a mounted pool or volume.',
	'snapshots.storagePage': 'Storage →',
	'snapshots.noPoolTitle': 'No pool is mounted',
	'snapshots.keptIn': 'Kept in',
	'snapshots.schedule': 'Schedule snapshots →',
	'snapshots.restored': 'Snapshot restored',
	// Before the path of the copy.
	'snapshots.restoredAt': 'The copy is at',
	'snapshots.of': 'Snapshots of {name}',
	'snapshots.loadError': 'Could not load the snapshots',
	'snapshots.empty': 'No snapshots yet',
	'snapshots.emptyHint': 'Create snapshot takes one now; the Scheduler takes them of pools on a schedule.',
	'snapshots.list': 'Snapshots',
	'snapshots.name': 'Name',
	'snapshots.created': 'Created',
	'snapshots.actions': 'Actions',
	'snapshots.restoreOne': 'Restore snapshot {name}',
	'snapshots.restoreAction': 'Restore…',
	'snapshots.deleteOne': 'Delete snapshot {name}',
	// After the prefix of a name that UNOS gives, in the pattern of the names of scheduled
	// snapshots (auto_) and of restored copies (restore_).
	'snapshots.dateTime': '<date>_<time>',

	// Create Snapshot: before the pool's or the volume's path, and before its .snapshots folder.
	'snapshots.new.title': 'Create snapshot',
	'snapshots.new.of': 'A read-only snapshot of',
	'snapshots.new.keptIn': ', kept in',
	'snapshots.new.pool':
		"It does not include the files in the pool's volumes: take snapshots of each volume as well. Scheduled snapshot tasks take pools only.",
	'snapshots.new.name': 'Snapshot name (optional)',
	'snapshots.new.namePlaceholder': 'Made from the date and time in UTC when empty',
	'snapshots.new.failed': 'Snapshot was not created',
	'snapshots.new.submit': 'Create',

	// Restore snapshot: before the snapshot's name, after it, after restore_<date>_<time>, and
	// after the path the copy is made in.
	'snapshots.restore.title': 'Restore snapshot',
	'snapshots.restore.before': 'Restore snapshot',
	'snapshots.restore.copy': '? A writable copy of it,',
	'snapshots.restore.madeIn': 'with the date and time in UTC, is made in',
	'snapshots.restore.after': '. Nothing is overwritten.',
	'snapshots.restore.poolCopy':
		'The copy is a volume of the pool too. When it is no longer needed, delete it under Restored copies below.',
	'snapshots.restore.volumeCopy':
		'The copy is a subvolume inside the volume, listed under Restored copies below. The volume cannot be deleted while it holds the copy: delete the copy there first.',
	'snapshots.restore.failed': 'Snapshot was not restored',
	'snapshots.restore.submit': 'Restore',

	// Delete Snapshot: before the snapshot's name, and after its path in parentheses.
	'snapshots.delete.title': 'Delete snapshot',
	'snapshots.delete.before': 'Delete snapshot',
	'snapshots.delete.after': '? This cannot be undone.',
	'snapshots.delete.failed': 'Snapshot was not deleted',
	'snapshots.delete.submit': 'Delete snapshot',

	// The copies that restores made right in the pool or volume chosen (ADR-0358).
	'snapshots.copies.of': 'Restored copies in {name}',
	'snapshots.copies.hint': 'Writable copies that Restore made here: copy what you need, then delete them.',
	'snapshots.copies.loadError': 'Could not load the restored copies',
	'snapshots.copies.empty': 'No restored copies here.',
	'snapshots.copies.list': 'Restored copies',
	'snapshots.copies.made': 'Restored',
	'snapshots.copies.deleteOne': 'Delete restored copy {name}',

	// Delete Restored Copy: before the copy's name, and after its path in parentheses.
	'snapshots.copies.delete.title': 'Delete restored copy',
	'snapshots.copies.delete.before': 'Delete the restored copy',
	'snapshots.copies.delete.after': '? Its files are deleted with it, and this cannot be undone.',
	'snapshots.copies.delete.inUse':
		'It is not deleted while a running container, an SMB share or an NFS export uses it.',
	'snapshots.copies.delete.failed': 'Restored copy was not deleted',
	'snapshots.copies.delete.submit': 'Delete copy'
};

export const ru: Translation<typeof en> = {
	'snapshots.title': 'Снимки',
	'snapshots.subtitle':
		'Копии пула или тома только для чтения. Снимок пула не включает его тома: делайте снимки и томов.',
	'snapshots.create': 'Создать снимок',
	'snapshots.createBlocked': 'Сначала смонтируйте пул на странице «Хранилище»',
	'snapshots.loadPoolsError': 'Не удалось загрузить пулы и тома',
	'snapshots.picker': 'Пул или том',
	'snapshots.pool': '{pool} (пул)',
	'snapshots.volume': '{pool}/{volume} (том)',
	'snapshots.volumesError': 'Не удалось прочитать тома пула {pool}',
	'snapshots.volumesErrorHint': 'Сам пул можно выбрать, но его томов нет в списке.',
	'snapshots.noPool': 'Снимки делаются со смонтированного пула или тома.',
	'snapshots.storagePage': 'Хранилище →',
	'snapshots.noPoolTitle': 'Нет смонтированных пулов',
	'snapshots.keptIn': 'Хранятся в',
	'snapshots.schedule': 'Снимки по расписанию →',
	'snapshots.restored': 'Снимок восстановлен',
	'snapshots.restoredAt': 'Путь к копии:',
	'snapshots.of': 'Снимки {name}',
	'snapshots.loadError': 'Не удалось загрузить снимки',
	'snapshots.empty': 'Снимков пока нет',
	'snapshots.emptyHint': 'Кнопка «Создать снимок» сделает его сейчас; Планировщик делает снимки пулов по расписанию.',
	'snapshots.list': 'Снимки',
	'snapshots.name': 'Имя',
	'snapshots.created': 'Создан',
	'snapshots.actions': 'Действия',
	'snapshots.restoreOne': 'Восстановить снимок {name}',
	'snapshots.restoreAction': 'Восстановить…',
	'snapshots.deleteOne': 'Удалить снимок {name}',
	'snapshots.dateTime': '<дата>_<время>',

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
		'Копия станет и томом пула. Когда она больше не нужна, удалите её в разделе «Восстановленные копии» ниже.',
	'snapshots.restore.volumeCopy':
		'Копия станет подтомом внутри тома и появится в разделе «Восстановленные копии» ниже. Том нельзя удалить, пока в нём есть копия: сначала удалите её там.',
	'snapshots.restore.failed': 'Снимок не восстановлен',
	'snapshots.restore.submit': 'Восстановить',

	'snapshots.delete.title': 'Удаление снимка',
	'snapshots.delete.before': 'Удалить снимок',
	'snapshots.delete.after': '? Это действие нельзя отменить.',
	'snapshots.delete.failed': 'Снимок не удалён',
	'snapshots.delete.submit': 'Удалить снимок',

	'snapshots.copies.of': 'Восстановленные копии в {name}',
	'snapshots.copies.hint': 'Копии для записи, созданные восстановлением: скопируйте нужное, затем удалите их.',
	'snapshots.copies.loadError': 'Не удалось загрузить восстановленные копии',
	'snapshots.copies.empty': 'Восстановленных копий здесь нет.',
	'snapshots.copies.list': 'Восстановленные копии',
	'snapshots.copies.made': 'Восстановлена',
	'snapshots.copies.deleteOne': 'Удалить восстановленную копию {name}',

	'snapshots.copies.delete.title': 'Удаление восстановленной копии',
	'snapshots.copies.delete.before': 'Удалить восстановленную копию',
	'snapshots.copies.delete.after': '? Её файлы будут удалены вместе с ней, это действие нельзя отменить.',
	'snapshots.copies.delete.inUse':
		'Копия не удаляется, пока её использует работающий контейнер, общая папка SMB или экспорт NFS.',
	'snapshots.copies.delete.failed': 'Восстановленная копия не удалена',
	'snapshots.copies.delete.submit': 'Удалить копию'
};
