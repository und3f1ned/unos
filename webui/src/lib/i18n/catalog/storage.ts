import type { Translation } from '../translate';

// The Storage page: src/routes/storage/+page.svelte, with the host's disks, the btrfs filesystems
// found on them, the pools and their scrubs, and the dialogs that create, mount, unmount, import
// and remove a pool and that list, create and delete its volumes. A disk's in-use reason
// (in_use_reason) is the server's message, and the names of btrfs (filesystems, RAID profiles) are
// shown as they come (ADR-0300).

export const en = {
	'storage.title': 'Storage',
	'storage.createPool': 'Create Pool',

	// A size that the page writes itself, by powers of 1024: {size} is the number, with one decimal.
	'storage.size.bytes': '{size} B',
	'storage.size.kb': '{size} KB',
	'storage.size.mb': '{size} MB',
	'storage.size.gb': '{size} GB',
	'storage.size.tb': '{size} TB',
	// A label or a name shown on its own, such as the label after a filesystem's type.
	'storage.quoted': '"{name}"',
	// What the page asks, and what UNOS refuses, of a new pool's or volume's name. The letters are
	// Latin ones only (POOL_NAME_PATTERN and VOLUME_NAME_PATTERN of src/lib/api/storage.ts): a
	// language with letters of its own says so.
	'storage.nameRule': "Use letters, numbers, '-' and '_' (up to 63), starting with a letter or number",

	'storage.disks.title': 'Physical Disks',
	'storage.disks.loadError': 'Could not load the disks',
	'storage.disks.none': 'No disks found',
	'storage.disks.column.device': 'Device',
	'storage.disks.column.model': 'Model',
	'storage.disks.column.size': 'Size',
	'storage.disks.column.type': 'Type',
	'storage.disks.column.contents': 'Contents',
	'storage.disks.column.mountedAt': 'Mounted at',
	'storage.disks.column.status': 'Status',
	'storage.disks.column.power': 'Power',
	'storage.disks.hdd': 'HDD',
	'storage.disks.ssd': 'SSD',
	// What a disk holds, as lsblk reports it: {type} is the partition table's, such as GPT or DOS.
	'storage.disks.partitionTable': '{type} partition table',
	// The same after a filesystem of the whole disk, in the middle of the list.
	'storage.disks.partitionTableAfter': '{type} partition table',
	'storage.disks.partitions': { one: '{n} partition', other: '{n} partitions' },
	'storage.disks.unrecognized': 'Unrecognized data',
	'storage.disks.empty': 'Empty',
	// A sleeping disk is not read for what it holds, so as not to wake it (ADR-0378).
	'storage.disks.notReadAsleep': 'Not read while the disk sleeps',
	// A disk's status; the reason below it is the server's (in_use_reason).
	'storage.disks.status.system': 'System',
	'storage.disks.status.inUse': 'In use',
	'storage.disks.status.pool': 'Pool "{pool}"',
	'storage.disks.status.hasData': 'Has data',
	'storage.disks.status.free': 'Free',
	// A disk's power, read without waking it (ADR-0378).
	'storage.disks.power.active': 'Active',
	'storage.disks.power.standby': 'Standby',
	'storage.disks.power.unknown': 'Unknown',

	// A disk's idle timeout (ADR-0378): the button shows it, and opens the dialog that sets it.
	'storage.spindown.off': 'Off',
	'storage.spindown.minutes': { one: '{n} minute', other: '{n} minutes' },
	'storage.spindown.hours': { one: '{n} hour', other: '{n} hours' },
	// Before the timeout in the button, for screen readers: {disk} is its path.
	'storage.spindown.of': 'Idle timeout of {disk}:',
	'storage.spindown.title': 'Idle Timeout',
	// Before the disk's path and model.
	'storage.spindown.disk': 'The disk spins down after this long without access:',
	'storage.spindown.label': 'Spin down after',
	'storage.spindown.hint':
		'Saving spins the disk up now. A disk that is woken often wears faster: choose a longer timeout for a disk in daily use.',
	'storage.spindown.failed': 'The idle timeout was not set',
	'storage.spindown.save': 'Save',

	// btrfs filesystems on the host's disks that are not pools yet (ADR-0283).
	'storage.found.title': 'Found on the disks',
	'storage.found.searchError': 'Could not look for pools to import',
	'storage.found.intro':
		"btrfs filesystems on the host's disks that are not pools yet. Importing one keeps its data: the disks are not formatted.",
	'storage.found.noLabel': 'No label',
	'storage.found.label': 'Label "{label}"',
	'storage.found.uuid': 'UUID {uuid}',
	// After the filesystem's devices.
	'storage.found.usage': '{size}, {used} used',
	// Why the filesystem is not a pool yet: its label is another filesystem's too, is no pool
	// name, or is missing.
	'storage.found.duplicate': 'Another btrfs filesystem is labelled "{label}" too: import this one under another name.',
	'storage.found.invalid': 'Its label is not a valid pool name: choose one for the pool.',
	'storage.found.missing': 'It has no label: choose a name for the pool.',
	'storage.found.missingDevices': {
		one: '{n} of its {total} devices is missing. Reconnect it to import the filesystem.',
		other: '{n} of its {total} devices are missing. Reconnect them to import the filesystem.'
	},
	'storage.found.import': 'Import',
	// For screen readers, after Import: the filesystem it imports.
	'storage.found.importOne': 'the filesystem {uuid}',

	'storage.pools.title': 'Storage Pools',
	'storage.pools.unsafeTitle': 'Unsafe RAID Configuration Detected',
	'storage.pools.unsafe':
		'One or more pools use BTRFS RAID5/RAID6, which has a known write-hole bug that can cause data corruption on power failure. Consider backing up and recreating the pool with a safe RAID level (RAID1, RAID1C3, or RAID1C4).',
	'storage.pools.loadError': 'Could not load the pools',
	'storage.pools.none': 'No pools configured',

	// A pool's health, as the server answers it (Pool.health).
	'storage.health.healthy': 'Healthy',
	'storage.health.degraded': 'Degraded',
	'storage.health.unmounted': 'Not mounted',
	'storage.health.error': 'Error',
	'storage.health.unknown': 'Unknown',

	// A pool's RAID profile as btrfs names its data profile (raid_level): names, the same in every
	// language unless a translation names them otherwise.
	'storage.profile.single': 'single',
	'storage.profile.dup': 'dup',
	'storage.profile.raid0': 'raid0',
	'storage.profile.raid1': 'raid1',
	'storage.profile.raid1c3': 'raid1c3',
	'storage.profile.raid1c4': 'raid1c4',
	'storage.profile.raid10': 'raid10',
	'storage.profile.raid5': 'raid5',
	'storage.profile.raid6': 'raid6',

	'storage.pool.notMountedAtBoot': 'Not mounted at boot',
	'storage.pool.mountedAtBoot': 'Mounted at boot',
	'storage.pool.inFstab': 'In fstab, but not mounted',
	'storage.pool.scrub': 'Scrub',
	'storage.pool.cancelScrub': 'Cancel Scrub',
	'storage.pool.volumes': 'Volumes',
	'storage.pool.mount': 'Mount',
	'storage.pool.unmount': 'Unmount',
	'storage.pool.remove': 'Remove pool {name}',
	// The fields of a pool's card, each before its value.
	'storage.pool.raid': 'RAID:',
	'storage.pool.used': 'Used:',
	'storage.pool.devices': 'Devices:',
	'storage.pool.mountpoint': 'Mount:',
	'storage.pool.unsafe': 'Unsafe',
	'storage.pool.unknown': 'unknown',
	'storage.pool.unknownNotMounted': 'unknown (not mounted)',
	'storage.pool.notMounted': 'Not mounted',
	'storage.pool.missingTitle': 'Pool is missing devices',
	'storage.pool.missing': {
		one: '{n} device is missing. Reconnect or replace it before the remaining redundancy is lost.',
		other: '{n} devices are missing. Reconnect or replace them before the remaining redundancy is lost.'
	},
	'storage.pool.forcedReadOnlyTitle': 'The filesystem is read-only',
	'storage.pool.forcedReadOnly':
		'Writes to this pool fail. btrfs stops writing after a serious error, such as an aborted transaction; check the kernel log (dmesg) for the cause.',
	'storage.pool.readOnlyTitle': 'Mounted read-only',
	'storage.pool.readOnly': 'Shares and apps cannot write to this pool until it is mounted read-write again.',
	'storage.pool.deviceErrorsTitle': 'Device errors reported',
	// Before the command that resets the counters.
	'storage.pool.deviceErrors': {
		one: "btrfs counted {n} read/write/checksum error on this pool's devices. Check the disks' SMART health and run a scrub. The counters keep their values until they are reset with",
		other:
			"btrfs counted {n} read/write/checksum errors on this pool's devices. Check the disks' SMART health and run a scrub. The counters keep their values until they are reset with"
	},
	'storage.pool.unreadableTitle': 'Pool state could not be read',
	'storage.pool.unreadable':
		"UNOS could not read the host mount table or this pool's btrfs error counters, so its health is unknown. Check the system logs.",

	// A pool's scrub, as btrfs reports it (ADR-0313).
	'storage.scrub.statusError': 'Could not read the scrub status',
	'storage.scrub.running': 'Scrub in progress',
	'storage.scrub.progress': 'Scrub progress',
	// {rate} is a size.
	'storage.scrub.rate': '{rate}/s',
	'storage.scrub.noErrors': 'Last scrub: No errors',
	'storage.scrub.errors': { one: 'Last scrub: {n} error(s)', other: 'Last scrub: {n} error(s)' },
	// A scrub that did not finish: {stopped} says how it ended.
	'storage.scrub.stopped': 'Last scrub: {stopped}',
	'storage.scrub.stoppedErrors': {
		one: 'Last scrub: {stopped}, {n} error(s) found',
		other: 'Last scrub: {stopped}, {n} error(s) found'
	},
	'storage.scrub.aborted': 'cancelled before it finished',
	'storage.scrub.interrupted': 'interrupted before it finished',

	'storage.create.title': 'Create Storage Pool',
	'storage.create.eraseTitle': 'This erases the selected disks',
	'storage.create.erase': 'Creating a pool formats the selected disks with btrfs.',
	'storage.create.eraseAll': 'ALL DATA on the selected disks will be erased.',
	'storage.create.name': 'Pool Name',
	// An example of a pool's name.
	'storage.create.namePlaceholder': 'data',
	'storage.create.raidLevel': 'RAID Level',
	'storage.create.devices': 'Select Devices',
	'storage.create.checkingDisks': 'Checking disks...',
	// After what a disk holds.
	'storage.create.memberOf': 'member of pool "{pool}"',
	// {reason} is the server's (in_use_reason), or one of the two below.
	'storage.create.unavailable': 'Not available: {reason}',
	'storage.create.systemDisk': 'System disk',
	'storage.create.inUse': 'In use',
	'storage.create.containsData': 'Contains data',
	'storage.create.selected': { one: 'Selected: {n} device(s)', other: 'Selected: {n} device(s)' },
	// {level} is the RAID level, such as RAID1.
	'storage.create.tooFew': '{level} requires at least {required} devices. You have selected {selected}.',
	'storage.create.dataTitle': 'Existing data will be destroyed',
	'storage.create.notEmpty': 'These disks are not empty:',
	'storage.create.eraseData': 'Erase the existing data on these disks',
	'storage.create.confirm': 'Type the pool name to confirm',
	'storage.create.confirmPlaceholder': 'pool name',
	'storage.create.mismatch': 'Does not match the pool name',
	'storage.create.failed': 'Pool was not created',
	'storage.create.cleared':
		'The selection has been cleared. Close this dialog and open it again to reload the disks.',
	'storage.create.reloaded':
		'The disk list has been reloaded and the selection cleared. Check the disks and select them again.',
	'storage.create.submit': 'Erase Disks & Create Pool',

	// The RAID levels of a new pool, and what each of them does.
	'storage.raid.single': 'Single (No RAID) — 1+ disk',
	'storage.raid.raid0': 'RAID 0 (Striping) — 2+ disks',
	'storage.raid.raid1': 'RAID 1 (Mirror, 2 copies) — 2+ disks',
	'storage.raid.raid1c3': 'RAID 1C3 (Mirror, 3 copies) — 3+ disks',
	'storage.raid.raid1c4': 'RAID 1C4 (Mirror, 4 copies) — 4+ disks',
	'storage.raid.raid10': 'RAID 10 (Mirrored stripes) — 4+ disks',
	'storage.raid.singleHint': 'No redundancy. All space available. A single disk failure loses all data.',
	'storage.raid.raid0Hint': 'Striped across disks for performance. No redundancy. Any disk failure loses all data.',
	'storage.raid.raid1Hint': 'Mirrored to 2 copies. Survives 1 disk failure. 50% space efficiency.',
	'storage.raid.raid1c3Hint': 'Mirrored to 3 copies. Survives 2 disk failures. 33% space efficiency.',
	'storage.raid.raid1c4Hint': 'Mirrored to 4 copies. Survives 3 disk failures. 25% space efficiency.',
	'storage.raid.raid10Hint': 'Striped mirrors. Survives 1 failure per mirror pair. 50% space efficiency.',

	'storage.mount.title': 'Mount Pool',
	// The question of the dialog, around the pool's name and its mountpoint: "Mount "<name>" at <path>?"
	'storage.mount.question': 'Mount',
	'storage.mount.at': 'at',
	'storage.mount.kept':
		'The disks are not formatted: the data on them is kept. UNOS also adds an /etc/fstab entry so the pool is mounted again after a reboot.',
	'storage.mount.failed': 'Mount failed',
	'storage.unmount.title': 'Unmount Pool',
	// The question of the dialog, before the pool's name: "Unmount "<name>"?"
	'storage.unmount.question': 'Unmount',
	// Around the pool's mountpoint.
	'storage.unmount.before': 'Shares, apps and scheduled tasks that use',
	'storage.unmount.after':
		'lose access until the pool is mounted again. The /etc/fstab entry is removed, so the pool is not mounted at boot. The data on the disks is not touched.',
	'storage.unmount.failed': 'Unmount failed',

	'storage.import.title': 'Import Pool',
	// The sentence of the dialog, around the filesystem's UUID, its label and its devices:
	// "Import the btrfs filesystem <uuid> (<label>) on <devices> as a pool."
	'storage.import.filesystem': 'Import the btrfs filesystem',
	'storage.import.noLabel': 'no label',
	'storage.import.label': 'label "{label}"',
	'storage.import.on': 'on',
	'storage.import.asPool': 'as a pool.',
	'storage.import.keptTitle': 'The disks are not formatted',
	// Around the pool's mountpoint.
	'storage.import.keptBefore':
		'The data on them is kept. UNOS gives the filesystem the pool name as its label, mounts it at',
	'storage.import.keptAfter':
		'by its UUID and adds an /etc/fstab entry, so the pool is mounted again after a reboot.',
	// The last part of the mountpoint while no name is entered.
	'storage.import.nameSlot': '<name>',
	'storage.import.name': 'Pool name',
	// An example of a pool's name.
	'storage.import.namePlaceholder': 'backup',
	'storage.import.nameTaken': 'A pool named "{name}" exists already. Choose another name.',
	'storage.import.labelTaken':
		'Another btrfs filesystem found on the disks is labelled "{name}". Choose another name.',
	'storage.import.failed': 'Pool was not imported',

	'storage.remove.title': 'Remove Pool',
	'storage.remove.warningTitle': 'Remove pool from UNOS',
	// Before the pool's name.
	'storage.remove.about': 'You are about to remove the storage pool',
	'storage.remove.kept':
		'UNOS unmounts it and removes its /etc/fstab entry. The disks are not wiped: the btrfs filesystem and its data stay on them, and the pool can be mounted again later. Shares and apps that use it stop working.',
	'storage.remove.details': 'Pool details:',
	'storage.remove.size': 'Size: {size}',
	'storage.remove.used': 'Used: {used}',
	'storage.remove.devices': 'Devices: {devices}',
	'storage.remove.mount': 'Mount: {mount}',
	'storage.remove.continue': 'Continue',
	'storage.remove.finalTitle': 'Final Confirmation',
	'storage.remove.typeName': 'To confirm, please type the pool name:',
	'storage.remove.confirm': 'Type pool name to confirm',
	'storage.remove.confirmPlaceholder': 'Enter pool name',
	'storage.remove.failed': 'Pool was not removed',
	'storage.remove.back': 'Back',
	'storage.remove.submit': 'Remove Pool',

	// A pool's volumes: btrfs subvolumes in its top folder (ADR-0243).
	'storage.volumes.title': 'Volumes on {pool}',
	'storage.volumes.titleNone': 'Volumes',
	// Around the pool's mountpoint.
	'storage.volumes.introBefore': 'A volume is a btrfs subvolume in',
	'storage.volumes.introAfter':
		'that can be shared on its own. Snapshots of the pool do not include the files in its volumes: take snapshots of each volume as well, on the Snapshots page. Scheduled snapshot tasks take pools only.',
	'storage.volumes.readOnly': 'Volumes cannot be created or deleted while the pool is mounted read-only.',
	'storage.volumes.forcedReadOnly': 'btrfs made it read-only after an error; check the kernel log (dmesg).',
	'storage.volumes.loading': 'Loading volumes...',
	'storage.volumes.loadError': 'Could not load the volumes',
	'storage.volumes.none': 'No volumes yet',
	'storage.volumes.deleteOne': 'Delete volume {name}',
	'storage.volumes.deleteTitle': 'Delete volume "{name}"?',
	// Around the volume's path.
	'storage.volumes.deleteBefore': 'All files in',
	'storage.volumes.deleteAfter':
		'are deleted with it, including those of stopped apps. This cannot be undone. While a running app or container, an SMB share or an NFS export uses the volume, it is not deleted.',
	'storage.volumes.confirm': 'Type the volume name to confirm',
	'storage.volumes.deleteFailed': 'Volume was not deleted',
	'storage.volumes.delete': 'Delete Volume',
	'storage.volumes.name': 'New volume name',
	// An example of a volume's name.
	'storage.volumes.namePlaceholder': 'documents',
	'storage.volumes.quota': 'Quota in GiB (optional)',
	'storage.volumes.quotaPlaceholder': 'No limit',
	// The field takes a number with a decimal point, in every language.
	'storage.volumes.quotaError': 'Enter a number of GiB greater than 0, such as 10 or 0.5 (at most {max})',
	'storage.volumes.quotaHint': 'Empty for no limit. A quota needs quotas enabled on the pool.',
	'storage.volumes.createFailed': 'Volume was not created',
	'storage.volumes.create': 'Create Volume',
	'storage.volumes.close': 'Close'
};

export const ru: Translation<typeof en> = {
	'storage.title': 'Хранилище',
	'storage.createPool': 'Создать пул',

	'storage.size.bytes': '{size} Б',
	'storage.size.kb': '{size} КБ',
	'storage.size.mb': '{size} МБ',
	'storage.size.gb': '{size} ГБ',
	'storage.size.tb': '{size} ТБ',
	'storage.quoted': '«{name}»',
	'storage.nameRule':
		'Используйте латинские буквы, цифры, «-» и «_» (до 63 символов), начиная с латинской буквы или цифры',

	'storage.disks.title': 'Физические диски',
	'storage.disks.loadError': 'Не удалось загрузить диски',
	'storage.disks.none': 'Диски не найдены',
	'storage.disks.column.device': 'Устройство',
	'storage.disks.column.model': 'Модель',
	'storage.disks.column.size': 'Размер',
	'storage.disks.column.type': 'Тип',
	'storage.disks.column.contents': 'Содержимое',
	'storage.disks.column.mountedAt': 'Точка монтирования',
	'storage.disks.column.status': 'Статус',
	'storage.disks.column.power': 'Питание',
	'storage.disks.hdd': 'HDD',
	'storage.disks.ssd': 'SSD',
	'storage.disks.partitionTable': 'Таблица разделов {type}',
	'storage.disks.partitionTableAfter': 'таблица разделов {type}',
	'storage.disks.partitions': { one: '{n} раздел', few: '{n} раздела', many: '{n} разделов' },
	'storage.disks.unrecognized': 'Нераспознанные данные',
	'storage.disks.empty': 'Пустой',
	'storage.disks.notReadAsleep': 'Не прочитано, пока диск спит',
	'storage.disks.status.system': 'Системный',
	'storage.disks.status.inUse': 'Используется',
	'storage.disks.status.pool': 'Пул «{pool}»',
	'storage.disks.status.hasData': 'Есть данные',
	'storage.disks.status.free': 'Свободен',
	'storage.disks.power.active': 'Работает',
	'storage.disks.power.standby': 'Спит',
	'storage.disks.power.unknown': 'Неизвестно',

	'storage.spindown.off': 'Выкл.',
	'storage.spindown.minutes': { one: '{n} минута', few: '{n} минуты', many: '{n} минут' },
	'storage.spindown.hours': { one: '{n} час', few: '{n} часа', many: '{n} часов' },
	'storage.spindown.of': 'Тайм-аут простоя {disk}:',
	'storage.spindown.title': 'Тайм-аут простоя',
	'storage.spindown.disk': 'Диск останавливается после такого времени без обращений:',
	'storage.spindown.label': 'Остановить через',
	'storage.spindown.hint':
		'Сохранение сейчас раскрутит диск. Диск, который часто будят, изнашивается быстрее: для диска, которым пользуются каждый день, выберите время подольше.',
	'storage.spindown.failed': 'Тайм-аут простоя не установлен',
	'storage.spindown.save': 'Сохранить',

	'storage.found.title': 'Найдено на дисках',
	'storage.found.searchError': 'Не удалось выполнить поиск пулов для импорта',
	'storage.found.intro':
		'Файловые системы btrfs на дисках хоста, которые ещё не стали пулами. При импорте данные сохраняются: диски не форматируются.',
	'storage.found.noLabel': 'Без метки',
	'storage.found.label': 'Метка «{label}»',
	'storage.found.uuid': 'UUID {uuid}',
	'storage.found.usage': '{size}, занято {used}',
	'storage.found.duplicate':
		'Метка «{label}» есть и у другой файловой системы btrfs: импортируйте эту под другим именем.',
	'storage.found.invalid': 'Её метка не подходит как имя пула: выберите имя для пула.',
	'storage.found.missing': 'У неё нет метки: выберите имя для пула.',
	'storage.found.missingDevices': {
		one: 'Не хватает {n} устройства из {total}. Подключите его, чтобы импортировать файловую систему.',
		few: 'Не хватает {n} устройств из {total}. Подключите их, чтобы импортировать файловую систему.',
		many: 'Не хватает {n} устройств из {total}. Подключите их, чтобы импортировать файловую систему.'
	},
	'storage.found.import': 'Импортировать',
	'storage.found.importOne': 'файловую систему {uuid}',

	'storage.pools.title': 'Пулы хранения',
	'storage.pools.unsafeTitle': 'Обнаружена небезопасная конфигурация RAID',
	'storage.pools.unsafe':
		'Один или несколько пулов используют RAID5/RAID6 в BTRFS, где есть известная ошибка «write hole»: при сбое питания данные могут быть повреждены. Рекомендуется сделать резервную копию и пересоздать пул с безопасным уровнем RAID (RAID1, RAID1C3 или RAID1C4).',
	'storage.pools.loadError': 'Не удалось загрузить пулы',
	'storage.pools.none': 'Пулы не настроены',

	'storage.health.healthy': 'Исправен',
	'storage.health.degraded': 'Деградирован',
	'storage.health.unmounted': 'Не смонтирован',
	'storage.health.error': 'Ошибка',
	'storage.health.unknown': 'Неизвестно',

	'storage.profile.single': 'single',
	'storage.profile.dup': 'dup',
	'storage.profile.raid0': 'raid0',
	'storage.profile.raid1': 'raid1',
	'storage.profile.raid1c3': 'raid1c3',
	'storage.profile.raid1c4': 'raid1c4',
	'storage.profile.raid10': 'raid10',
	'storage.profile.raid5': 'raid5',
	'storage.profile.raid6': 'raid6',

	'storage.pool.notMountedAtBoot': 'Не монтируется при загрузке',
	'storage.pool.mountedAtBoot': 'Монтируется при загрузке',
	'storage.pool.inFstab': 'Есть в fstab, но не смонтирован',
	'storage.pool.scrub': 'Проверить целостность',
	'storage.pool.cancelScrub': 'Отменить проверку',
	'storage.pool.volumes': 'Тома',
	'storage.pool.mount': 'Смонтировать',
	'storage.pool.unmount': 'Отмонтировать',
	'storage.pool.remove': 'Убрать пул {name}',
	'storage.pool.raid': 'RAID:',
	'storage.pool.used': 'Занято:',
	'storage.pool.devices': 'Устройства:',
	'storage.pool.mountpoint': 'Точка монтирования:',
	'storage.pool.unsafe': 'Небезопасно',
	'storage.pool.unknown': 'неизвестно',
	'storage.pool.unknownNotMounted': 'неизвестно (не смонтирован)',
	'storage.pool.notMounted': 'Не смонтирован',
	'storage.pool.missingTitle': 'В пуле не хватает устройств',
	'storage.pool.missing': {
		one: 'Не хватает {n} устройства. Подключите или замените его, пока не потеряна оставшаяся избыточность.',
		few: 'Не хватает {n} устройств. Подключите или замените их, пока не потеряна оставшаяся избыточность.',
		many: 'Не хватает {n} устройств. Подключите или замените их, пока не потеряна оставшаяся избыточность.'
	},
	'storage.pool.forcedReadOnlyTitle': 'Файловая система только для чтения',
	'storage.pool.forcedReadOnly':
		'Запись в этот пул не удаётся. btrfs прекращает запись после серьёзной ошибки, например прерванной транзакции; причину ищите в журнале ядра (dmesg).',
	'storage.pool.readOnlyTitle': 'Смонтирован только для чтения',
	'storage.pool.readOnly':
		'Общие папки и приложения не могут записывать в этот пул, пока он снова не будет смонтирован для чтения и записи.',
	'storage.pool.deviceErrorsTitle': 'Устройства сообщили об ошибках',
	'storage.pool.deviceErrors': {
		one: 'btrfs насчитал {n} ошибку чтения, записи или контрольной суммы на устройствах этого пула. Проверьте состояние SMART дисков и запустите проверку целостности. Счётчики сохраняют значения, пока их не сбросят командой',
		few: 'btrfs насчитал {n} ошибки чтения, записи или контрольной суммы на устройствах этого пула. Проверьте состояние SMART дисков и запустите проверку целостности. Счётчики сохраняют значения, пока их не сбросят командой',
		many: 'btrfs насчитал {n} ошибок чтения, записи или контрольной суммы на устройствах этого пула. Проверьте состояние SMART дисков и запустите проверку целостности. Счётчики сохраняют значения, пока их не сбросят командой'
	},
	'storage.pool.unreadableTitle': 'Не удалось прочитать состояние пула',
	'storage.pool.unreadable':
		'UNOS не удалось прочитать таблицу монтирования хоста или счётчики ошибок btrfs этого пула, поэтому его состояние неизвестно. Проверьте системные журналы.',

	'storage.scrub.statusError': 'Не удалось прочитать состояние проверки целостности',
	'storage.scrub.running': 'Идёт проверка целостности',
	'storage.scrub.progress': 'Ход проверки целостности',
	'storage.scrub.rate': '{rate}/с',
	'storage.scrub.noErrors': 'Последняя проверка: ошибок нет',
	'storage.scrub.errors': {
		one: 'Последняя проверка: {n} ошибка',
		few: 'Последняя проверка: {n} ошибки',
		many: 'Последняя проверка: {n} ошибок'
	},
	'storage.scrub.stopped': 'Последняя проверка: {stopped}',
	'storage.scrub.stoppedErrors': {
		one: 'Последняя проверка: {stopped}, найдена {n} ошибка',
		few: 'Последняя проверка: {stopped}, найдены {n} ошибки',
		many: 'Последняя проверка: {stopped}, найдено {n} ошибок'
	},
	'storage.scrub.aborted': 'отменена до завершения',
	'storage.scrub.interrupted': 'прервана до завершения',

	'storage.create.title': 'Новый пул хранения',
	'storage.create.eraseTitle': 'Выбранные диски будут стёрты',
	'storage.create.erase': 'При создании пула выбранные диски форматируются в btrfs.',
	'storage.create.eraseAll': 'ВСЕ ДАННЫЕ на выбранных дисках будут стёрты.',
	'storage.create.name': 'Имя пула',
	'storage.create.namePlaceholder': 'data',
	'storage.create.raidLevel': 'Уровень RAID',
	'storage.create.devices': 'Выберите устройства',
	'storage.create.checkingDisks': 'Проверка дисков...',
	'storage.create.memberOf': 'входит в пул «{pool}»',
	'storage.create.unavailable': 'Недоступен: {reason}',
	'storage.create.systemDisk': 'Системный диск',
	'storage.create.inUse': 'Используется',
	'storage.create.containsData': 'Содержит данные',
	'storage.create.selected': {
		one: 'Выбрано: {n} устройство',
		few: 'Выбрано: {n} устройства',
		many: 'Выбрано: {n} устройств'
	},
	'storage.create.tooFew': 'Для {level} нужно не меньше {required} устройств. Выбрано: {selected}.',
	'storage.create.dataTitle': 'Существующие данные будут уничтожены',
	'storage.create.notEmpty': 'Эти диски не пусты:',
	'storage.create.eraseData': 'Стереть существующие данные на этих дисках',
	'storage.create.confirm': 'Введите имя пула для подтверждения',
	'storage.create.confirmPlaceholder': 'имя пула',
	'storage.create.mismatch': 'Не совпадает с именем пула',
	'storage.create.failed': 'Не удалось создать пул',
	'storage.create.cleared': 'Выбор сброшен. Закройте это окно и откройте его снова, чтобы заново загрузить диски.',
	'storage.create.reloaded': 'Список дисков загружен заново, а выбор сброшен. Проверьте диски и выберите их снова.',
	'storage.create.submit': 'Стереть диски и создать пул',

	'storage.raid.single': 'Single (без RAID) — от 1 диска',
	'storage.raid.raid0': 'RAID 0 (чередование) — от 2 дисков',
	'storage.raid.raid1': 'RAID 1 (зеркало, 2 копии) — от 2 дисков',
	'storage.raid.raid1c3': 'RAID 1C3 (зеркало, 3 копии) — от 3 дисков',
	'storage.raid.raid1c4': 'RAID 1C4 (зеркало, 4 копии) — от 4 дисков',
	'storage.raid.raid10': 'RAID 10 (зеркальное чередование) — от 4 дисков',
	'storage.raid.singleHint':
		'Без избыточности. Доступно всё пространство. Отказ одного диска приводит к потере всех данных.',
	'storage.raid.raid0Hint':
		'Данные чередуются между дисками ради скорости. Без избыточности. Отказ любого диска приводит к потере всех данных.',
	'storage.raid.raid1Hint': 'Зеркалирование в 2 копии. Выдерживает отказ 1 диска. Полезный объём: 50%.',
	'storage.raid.raid1c3Hint': 'Зеркалирование в 3 копии. Выдерживает отказ 2 дисков. Полезный объём: 33%.',
	'storage.raid.raid1c4Hint': 'Зеркалирование в 4 копии. Выдерживает отказ 3 дисков. Полезный объём: 25%.',
	'storage.raid.raid10Hint':
		'Чередование зеркал. Выдерживает отказ 1 диска в каждой зеркальной паре. Полезный объём: 50%.',

	'storage.mount.title': 'Смонтировать пул',
	'storage.mount.question': 'Смонтировать',
	'storage.mount.at': 'в',
	'storage.mount.kept':
		'Диски не форматируются: данные на них сохраняются. UNOS также добавит запись в /etc/fstab, чтобы пул снова монтировался после перезагрузки.',
	'storage.mount.failed': 'Не удалось смонтировать пул',
	'storage.unmount.title': 'Отмонтировать пул',
	'storage.unmount.question': 'Отмонтировать',
	'storage.unmount.before': 'Общие папки, приложения и задачи по расписанию теряют доступ к',
	'storage.unmount.after':
		'до тех пор, пока пул снова не будет смонтирован. Запись в /etc/fstab удаляется, поэтому при загрузке пул не монтируется. Данные на дисках не затрагиваются.',
	'storage.unmount.failed': 'Не удалось отмонтировать пул',

	'storage.import.title': 'Импорт пула',
	'storage.import.filesystem': 'Импортировать файловую систему btrfs',
	'storage.import.noLabel': 'без метки',
	'storage.import.label': 'метка «{label}»',
	'storage.import.on': 'на',
	'storage.import.asPool': 'как пул.',
	'storage.import.keptTitle': 'Диски не форматируются',
	'storage.import.keptBefore':
		'Данные на них сохраняются. UNOS сделает имя пула меткой файловой системы, смонтирует её в',
	'storage.import.keptAfter':
		'по UUID и добавит запись в /etc/fstab, чтобы пул снова монтировался после перезагрузки.',
	'storage.import.nameSlot': '<имя>',
	'storage.import.name': 'Имя пула',
	'storage.import.namePlaceholder': 'backup',
	'storage.import.nameTaken': 'Пул «{name}» уже есть. Выберите другое имя.',
	'storage.import.labelTaken': 'У другой файловой системы btrfs на дисках уже есть метка «{name}». Выберите другое имя.',
	'storage.import.failed': 'Не удалось импортировать пул',

	'storage.remove.title': 'Убрать пул',
	'storage.remove.warningTitle': 'Убрать пул из UNOS',
	'storage.remove.about': 'Вы собираетесь убрать пул хранения',
	'storage.remove.kept':
		'UNOS отмонтирует его и удалит его запись из /etc/fstab. Диски не очищаются: файловая система btrfs и её данные остаются на них, и пул можно будет снова смонтировать позже. Общие папки и приложения, которые его используют, перестанут работать.',
	'storage.remove.details': 'Сведения о пуле:',
	'storage.remove.size': 'Размер: {size}',
	'storage.remove.used': 'Занято: {used}',
	'storage.remove.devices': 'Устройства: {devices}',
	'storage.remove.mount': 'Точка монтирования: {mount}',
	'storage.remove.continue': 'Продолжить',
	'storage.remove.finalTitle': 'Последнее подтверждение',
	'storage.remove.typeName': 'Для подтверждения введите имя пула:',
	'storage.remove.confirm': 'Введите имя пула для подтверждения',
	'storage.remove.confirmPlaceholder': 'Введите имя пула',
	'storage.remove.failed': 'Не удалось убрать пул',
	'storage.remove.back': 'Назад',
	'storage.remove.submit': 'Убрать пул',

	'storage.volumes.title': 'Тома в пуле {pool}',
	'storage.volumes.titleNone': 'Тома',
	'storage.volumes.introBefore': 'Том — это подтом btrfs в',
	'storage.volumes.introAfter':
		'— его можно сделать общей папкой отдельно от пула. Снимки пула не включают файлы его томов: делайте снимки каждого тома тоже, на странице «Снимки». Задачи по расписанию делают снимки только пулов.',
	'storage.volumes.readOnly': 'Тома нельзя создавать и удалять, пока пул смонтирован только для чтения.',
	'storage.volumes.forcedReadOnly':
		'btrfs перевёл его в режим только для чтения после ошибки; проверьте журнал ядра (dmesg).',
	'storage.volumes.loading': 'Загрузка томов...',
	'storage.volumes.loadError': 'Не удалось загрузить тома',
	'storage.volumes.none': 'Томов пока нет',
	'storage.volumes.deleteOne': 'Удалить том {name}',
	'storage.volumes.deleteTitle': 'Удалить том «{name}»?',
	'storage.volumes.deleteBefore': 'Все файлы в',
	'storage.volumes.deleteAfter':
		'будут удалены вместе с ним, включая файлы остановленных приложений. Это нельзя отменить. Пока том используется работающим приложением или контейнером, общей папкой SMB или экспортом NFS, он не удаляется.',
	'storage.volumes.confirm': 'Введите имя тома для подтверждения',
	'storage.volumes.deleteFailed': 'Не удалось удалить том',
	'storage.volumes.delete': 'Удалить том',
	'storage.volumes.name': 'Имя нового тома',
	'storage.volumes.namePlaceholder': 'documents',
	'storage.volumes.quota': 'Квота в ГиБ (необязательно)',
	'storage.volumes.quotaPlaceholder': 'Без ограничения',
	'storage.volumes.quotaError': 'Введите число ГиБ больше 0, например 10 или 0.5 (не больше {max})',
	'storage.volumes.quotaHint': 'Оставьте пустым, чтобы не ограничивать. Для квоты в пуле должны быть включены квоты.',
	'storage.volumes.createFailed': 'Не удалось создать том',
	'storage.volumes.create': 'Создать том',
	'storage.volumes.close': 'Закрыть'
};
