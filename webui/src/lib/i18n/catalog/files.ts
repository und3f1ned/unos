import type { Translation } from '../translate';

// Files: the file manager (src/routes/files/+page.svelte) and its trash
// (src/routes/files/trash/+page.svelte, ADR-0259). The messages of the server stay English.

export const en = {
	'files.title': 'File Browser',
	'files.upload': 'Upload',
	'files.newFolder': 'New Folder',
	'files.pools': 'Pools',
	'files.selectedItems': 'Selected items',
	'files.selected': '{n} selected',
	'files.download': 'Download',
	'files.copy': 'Copy',
	'files.move': 'Move',
	'files.rename': 'Rename',
	'files.delete': 'Delete',
	'files.pending.copy': '{n} item(s) to copy. Open the destination folder, then choose Copy here.',
	'files.pending.move': '{n} item(s) to move. Open the destination folder, then choose Move here.',
	'files.copyHere': 'Copy here',
	'files.moveHere': 'Move here',
	'files.folderName': 'Folder name',
	'files.create': 'Create',
	'files.column.name': 'Name',
	'files.column.size': 'Size',
	'files.column.modified': 'Modified',
	'files.contents': 'Folder contents',
	'files.up': 'Up one folder',
	'files.select': 'Select {name}',
	'files.newName': 'New name for {name}',
	'files.openItem': 'Open {name}',
	'files.downloadItem': 'Download {name}',
	'files.itemCount': '{n} items',

	'files.error.open': 'Could not open the folder',
	'files.error.download': 'Could not download {name}',
	'files.error.create': 'Could not create the folder',
	'files.error.copy': 'Could not copy',
	'files.error.move': 'Could not move',
	'files.error.rename': 'Could not rename',
	'files.error.upload': 'Could not upload {files}',
	'files.error.toTrash': 'Could not move to the trash',
	'files.error.forGood': 'Could not delete for good',

	// The Delete dialog: one item, or more than one.
	'files.delete.title': { one: 'Delete {n} item', other: 'Delete {n} items' },
	'files.delete.one':
		'Move the selected item to the trash? You can restore it on the Trash page until it is deleted for good; until then it still takes up space in its pool.',
	'files.delete.many': {
		one: 'Move the {n} selected item to the trash? You can restore it on the Trash page until it is deleted for good; until then it still takes up space in its pool.',
		other:
			'Move the {n} selected items to the trash? You can restore them on the Trash page until they are deleted for good; until then they still take up space in their pool.'
	},
	'files.delete.forGoodOne':
		'Delete for good deletes it at once, without the trash, and frees its space. This cannot be undone.',
	'files.delete.forGoodMany':
		'Delete for good deletes them at once, without the trash, and frees their space. This cannot be undone.',
	'files.deleteForGood': 'Delete for good',
	'files.moveToTrash': 'Move to trash',

	// The hidden files that uploads left when the supervisor stopped (ADR-0350): the notice and its dialog.
	'files.leftovers.title': 'Leftovers of interrupted uploads',
	'files.leftovers.found': {
		one: '{n} hidden file that an upload left when the supervisor stopped in the middle of it takes {size}.',
		other: '{n} hidden files that uploads left when the supervisor stopped in the middle of them take {size}.'
	},
	'files.leftovers.hint':
		'They hold the part of a file that had arrived, and nothing uses them: upload those files again.',
	'files.leftovers.remove': 'Remove…',
	'files.leftovers.dialogTitle': {
		one: 'Remove {n} leftover of an upload',
		other: 'Remove {n} leftovers of uploads'
	},
	'files.leftovers.confirm': {
		one: 'Remove this file for good? This cannot be undone.',
		other: 'Remove these {n} files for good? This cannot be undone.'
	},
	'files.leftovers.file': '{size}, last written {time}',
	'files.leftovers.removeForGood': 'Remove for good',
	'files.leftovers.failed': 'Could not remove',

	// The units of a size, from bytes up (ADR-0297).
	'files.unit.b': 'B',
	'files.unit.kb': 'KB',
	'files.unit.mb': 'MB',
	'files.unit.gb': 'GB',
	'files.unit.tb': 'TB',

	'files.trash.title': 'Trash',
	'files.trash.files': 'Files',
	'files.trash.empty': 'Empty trash',
	'files.trash.kept': {
		one: 'Items deleted in Files wait here, in the trash of their pool or volume, and are deleted for good {n} day after they were deleted.',
		other:
			'Items deleted in Files wait here, in the trash of their pool or volume, and are deleted for good {n} days after they were deleted.'
	},
	'files.trash.keptUnknown':
		'Items deleted in Files wait here, in the trash of their pool or volume, and are deleted for good a number of days after they were deleted.',
	'files.trash.space':
		'Until then they take up space in their pool: delete them for good to free it at once. Restore puts an item back in the folder it was deleted from; it stays here while that folder is gone or its name is taken there.',
	'files.trash.loadError': 'Could not load the trash',
	'files.trash.problem': 'A trash could not be read',
	'files.trash.restore': 'Restore',
	'files.trash.emptyTitle': 'The trash is empty',
	'files.trash.emptyHint': 'What you delete in Files comes here.',
	'files.trash.items': 'Deleted items',
	'files.trash.column.selected': 'Selected',
	'files.trash.column.from': 'Deleted from',
	'files.trash.column.deleted': 'Deleted',
	'files.trash.column.keptUntil': 'Kept until',
	'files.trash.notKnown': 'Not known',
	'files.trash.select': 'Select {name}, deleted from {folder} on {time}',
	'files.trash.selectUnknown': 'Select {name}, deleted from an unknown folder on {time}',
	'files.trash.restoredTo': 'Restored to {path}.',
	'files.trash.restored': {
		one: 'Restored {n} item to where it was deleted from.',
		other: 'Restored {n} items to where they were deleted from.'
	},
	'files.trash.restoreFailed': 'Could not restore: {message}',
	'files.trash.deleted': { one: 'Deleted {n} item for good.', other: 'Deleted {n} items for good.' },
	'files.trash.confirmEmpty':
		'Delete everything in the trash for good, in every pool and volume? This cannot be undone.',
	'files.trash.confirmSelected': {
		one: 'Delete the {n} selected item for good? This cannot be undone.',
		other: 'Delete the {n} selected items for good? This cannot be undone.'
	},
	'files.trash.deleteFailed': 'Could not delete'
};

export const ru: Translation<typeof en> = {
	'files.title': 'Файловый менеджер',
	'files.upload': 'Загрузить',
	'files.newFolder': 'Новая папка',
	'files.pools': 'Пулы',
	'files.selectedItems': 'Выбранные элементы',
	'files.selected': 'Выбрано: {n}',
	'files.download': 'Скачать',
	'files.copy': 'Копировать',
	'files.move': 'Переместить',
	'files.rename': 'Переименовать',
	'files.delete': 'Удалить',
	'files.pending.copy':
		'Выбрано для копирования: {n}. Откройте папку назначения и нажмите «Копировать сюда».',
	'files.pending.move':
		'Выбрано для перемещения: {n}. Откройте папку назначения и нажмите «Переместить сюда».',
	'files.copyHere': 'Копировать сюда',
	'files.moveHere': 'Переместить сюда',
	'files.folderName': 'Имя папки',
	'files.create': 'Создать',
	'files.column.name': 'Имя',
	'files.column.size': 'Размер',
	'files.column.modified': 'Изменён',
	'files.contents': 'Содержимое папки',
	'files.up': 'На уровень выше',
	'files.select': 'Выбрать {name}',
	'files.newName': 'Новое имя для {name}',
	'files.openItem': 'Открыть {name}',
	'files.downloadItem': 'Скачать {name}',
	'files.itemCount': 'Элементов: {n}',

	'files.error.open': 'Не удалось открыть папку',
	'files.error.download': 'Не удалось скачать {name}',
	'files.error.create': 'Не удалось создать папку',
	'files.error.copy': 'Не удалось скопировать',
	'files.error.move': 'Не удалось переместить',
	'files.error.rename': 'Не удалось переименовать',
	'files.error.upload': 'Не удалось загрузить {files}',
	'files.error.toTrash': 'Не удалось переместить в корзину',
	'files.error.forGood': 'Не удалось удалить навсегда',

	'files.delete.title': {
		one: 'Удалить {n} элемент',
		few: 'Удалить {n} элемента',
		many: 'Удалить {n} элементов'
	},
	'files.delete.one':
		'Переместить выбранный элемент в корзину? Его можно восстановить на странице «Корзина», пока он не удалён навсегда; до тех пор он по-прежнему занимает место в своём пуле.',
	'files.delete.many': {
		one: 'Переместить {n} выбранный элемент в корзину? Их можно восстановить на странице «Корзина», пока они не удалены навсегда; до тех пор они по-прежнему занимают место в своём пуле.',
		few: 'Переместить {n} выбранных элемента в корзину? Их можно восстановить на странице «Корзина», пока они не удалены навсегда; до тех пор они по-прежнему занимают место в своём пуле.',
		many: 'Переместить {n} выбранных элементов в корзину? Их можно восстановить на странице «Корзина», пока они не удалены навсегда; до тех пор они по-прежнему занимают место в своём пуле.'
	},
	'files.delete.forGoodOne':
		'«Удалить навсегда» удаляет его сразу, минуя корзину, и освобождает занятое им место. Это нельзя отменить.',
	'files.delete.forGoodMany':
		'«Удалить навсегда» удаляет их сразу, минуя корзину, и освобождает занятое ими место. Это нельзя отменить.',
	'files.deleteForGood': 'Удалить навсегда',
	'files.moveToTrash': 'Переместить в корзину',

	'files.leftovers.title': 'Остатки прерванных загрузок',
	'files.leftovers.found': {
		one: 'Скрытые файлы, которые оставили загрузки, прерванные остановкой супервизора: {n} файл, {size}.',
		few: 'Скрытые файлы, которые оставили загрузки, прерванные остановкой супервизора: {n} файла, {size}.',
		many: 'Скрытые файлы, которые оставили загрузки, прерванные остановкой супервизора: {n} файлов, {size}.'
	},
	'files.leftovers.hint':
		'В них часть файла, которая успела прийти, и они ничему не нужны: загрузите эти файлы снова.',
	'files.leftovers.remove': 'Удалить…',
	'files.leftovers.dialogTitle': {
		one: 'Удалить {n} остаток загрузки',
		few: 'Удалить {n} остатка загрузок',
		many: 'Удалить {n} остатков загрузок'
	},
	'files.leftovers.confirm': {
		one: 'Удалить {n} файл навсегда? Это нельзя отменить.',
		few: 'Удалить {n} файла навсегда? Это нельзя отменить.',
		many: 'Удалить {n} файлов навсегда? Это нельзя отменить.'
	},
	'files.leftovers.file': '{size}, последняя запись {time}',
	'files.leftovers.removeForGood': 'Удалить навсегда',
	'files.leftovers.failed': 'Не удалось удалить',

	'files.unit.b': 'Б',
	'files.unit.kb': 'КБ',
	'files.unit.mb': 'МБ',
	'files.unit.gb': 'ГБ',
	'files.unit.tb': 'ТБ',

	'files.trash.title': 'Корзина',
	'files.trash.files': 'Файлы',
	'files.trash.empty': 'Очистить корзину',
	'files.trash.kept': {
		one: 'Элементы, удалённые в «Файлах», ждут здесь, в корзине своего пула или тома, и удаляются навсегда через {n} день после удаления.',
		few: 'Элементы, удалённые в «Файлах», ждут здесь, в корзине своего пула или тома, и удаляются навсегда через {n} дня после удаления.',
		many: 'Элементы, удалённые в «Файлах», ждут здесь, в корзине своего пула или тома, и удаляются навсегда через {n} дней после удаления.'
	},
	'files.trash.keptUnknown':
		'Элементы, удалённые в «Файлах», ждут здесь, в корзине своего пула или тома, и удаляются навсегда через заданное число дней после удаления.',
	'files.trash.space':
		'До тех пор они занимают место в своём пуле: удалите их навсегда, чтобы сразу освободить его. «Восстановить» возвращает элемент в папку, из которой он был удалён; он остаётся здесь, пока этой папки нет или его имя в ней занято.',
	'files.trash.loadError': 'Не удалось загрузить корзину',
	'files.trash.problem': 'Не удалось прочитать одну из корзин',
	'files.trash.restore': 'Восстановить',
	'files.trash.emptyTitle': 'Корзина пуста',
	'files.trash.emptyHint': 'Сюда попадает то, что вы удаляете в «Файлах».',
	'files.trash.items': 'Удалённые элементы',
	'files.trash.column.selected': 'Выбрано',
	'files.trash.column.from': 'Откуда удалён',
	'files.trash.column.deleted': 'Удалён',
	'files.trash.column.keptUntil': 'Хранится до',
	'files.trash.notKnown': 'Неизвестно',
	'files.trash.select': 'Выбрать {name}, удалён из {folder} {time}',
	'files.trash.selectUnknown': 'Выбрать {name}, удалён из неизвестной папки {time}',
	'files.trash.restoredTo': 'Восстановлено: {path}.',
	'files.trash.restored': {
		one: '{n} элемент восстановлен туда, откуда был удалён.',
		few: '{n} элемента восстановлены туда, откуда были удалены.',
		many: '{n} элементов восстановлены туда, откуда были удалены.'
	},
	'files.trash.restoreFailed': 'Не удалось восстановить: {message}',
	'files.trash.deleted': {
		one: '{n} элемент удалён навсегда.',
		few: '{n} элемента удалены навсегда.',
		many: '{n} элементов удалены навсегда.'
	},
	'files.trash.confirmEmpty': 'Удалить навсегда всё, что есть в корзине, во всех пулах и томах? Это нельзя отменить.',
	'files.trash.confirmSelected': {
		one: 'Удалить навсегда {n} выбранный элемент? Это нельзя отменить.',
		few: 'Удалить навсегда {n} выбранных элемента? Это нельзя отменить.',
		many: 'Удалить навсегда {n} выбранных элементов? Это нельзя отменить.'
	},
	'files.trash.deleteFailed': 'Не удалось удалить'
};
