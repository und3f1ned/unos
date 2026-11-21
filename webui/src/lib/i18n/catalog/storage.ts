import type { Translation } from '../translate';

// The Storage page: src/routes/storage/+page.svelte, with the host's disks, the btrfs filesystems
// found on them, the pools and their scrubs, and the dialogs that create, mount, unmount, import
// and remove a pool and that list, create and delete its volumes. A disk's in-use reason
// (in_use_reason) is the server's message, and the names of btrfs (filesystems, RAID profiles) are
// shown as they come (ADR-0401).

export const en = {
	'storage.title': 'Storage',
	'storage.createPool': 'Create pool',
	'storage.createBlocked': 'The disks could not be read: Refresh to try again.',

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

	'storage.disks.title': 'Disks',
	'storage.disks.loadError': 'Could not load the disks',
	'storage.disks.none': 'No disks found',
	'storage.disks.column.device': 'Device',
	'storage.disks.column.model': 'Model',
	'storage.disks.column.size': 'Size',
	'storage.disks.column.type': 'Type',
	'storage.disks.column.contents': 'Contents',
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
	'storage.disks.power.active': 'Awake',
	'storage.disks.power.standby': 'Asleep',
	'storage.disks.power.unknown': 'Unknown',
	'storage.disks.idleTimeout': 'Idle timeout',

	// A disk's idle timeout (ADR-0378): the button shows it, and opens the dialog that sets it.
	'storage.spindown.off': 'Off',
	'storage.spindown.minutes': { one: '{n} minute', other: '{n} minutes' },
	'storage.spindown.hours': { one: '{n} hour', other: '{n} hours' },
	// Before the timeout in the button, for screen readers: {disk} is its path.
	'storage.spindown.of': 'Idle timeout of {disk}:',
	'storage.spindown.title': 'Idle timeout',
	// Before the disk's path and model.
	'storage.spindown.disk': 'The disk spins down after this long without access:',
	'storage.spindown.label': 'Spin down after',
	'storage.spindown.hint':
		'Saving spins the disk up now. A disk that is woken often wears faster: choose a longer timeout for a disk in daily use.',
	// The disk's own automatic offline scans, which UNOS turns off while it has a timeout (ADR-0378).
	'storage.spindown.scans':
		'While the disk has a timeout, UNOS turns off its own automatic offline scans (a surface scan that the disk runs every few hours, during which it does not sleep) and turns them on again when the timeout is removed. Schedule a scrub of its pool (System, Scheduler) to read its data instead; a long SMART test reads the whole surface, but the SMART tests of the Scheduler are short ones.',
	'storage.spindown.failed': 'The idle timeout was not set',
	// Above the list of disks, before the server's warning: the timeout was set all the same.
	'storage.spindown.saved': 'The idle timeout was saved',
	'storage.spindown.save': 'Save',

	// btrfs filesystems on the host's disks that are not pools yet (ADR-0283).
	'storage.found.title': 'Found on the disks',
	'storage.found.searchError': 'Could not look for pools to import',
	'storage.found.intro': "btrfs filesystems on the host's disks that are not pools yet. Importing keeps their data.",
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
	'storage.found.import': 'Import…',
	// For screen readers, after Import: the filesystem it imports.
	'storage.found.importOne': 'the filesystem {uuid}',

	'storage.pools.title': 'Pools',
	'storage.pools.unsafeTitle': 'RAID 5 and 6 are unsafe in btrfs',
	'storage.pools.unsafe':
		'A power failure can corrupt data. Back up the pool and create it again as RAID 1, 1C3 or 1C4.',
	'storage.pools.loadError': 'Could not load the pools',
	'storage.pools.none': 'No pools yet',
	'storage.pools.noneHint': 'Create pool makes one from the free disks.',
	'storage.pools.freeDisks': {
		one: '{n} disk is free for one: Create pool makes it.',
		other: '{n} disks are free for one: Create pool makes it.'
	},

	// A pool's health, as the server answers it (Pool.health).
	'storage.health.healthy': 'Healthy',
	'storage.health.degraded': 'Degraded',
	'storage.health.unmounted': 'Not mounted',
	'storage.health.error': 'Error',
	'storage.health.unknown': 'Unknown',

	// A pool's RAID profile as btrfs names its data profile (raid_level): names, the same in every
	// language unless a translation names them otherwise.
	'storage.profile.single': 'Single',
	'storage.profile.dup': 'DUP (2 copies on one disk)',
	'storage.profile.raid0': 'RAID 0 (striped)',
	'storage.profile.raid1': 'RAID 1 (2 copies)',
	'storage.profile.raid1c3': 'RAID 1C3 (3 copies)',
	'storage.profile.raid1c4': 'RAID 1C4 (4 copies)',
	'storage.profile.raid10': 'RAID 10 (striped mirrors)',
	'storage.profile.raid5': 'RAID 5',
	'storage.profile.raid6': 'RAID 6',

	'storage.pool.notMountedAtBoot': 'Not mounted at boot',
	'storage.pool.mountedAtBoot': 'Mounted at boot',
	'storage.pool.inFstab': 'In fstab, but not mounted',
	'storage.pool.scrub': 'Scrub',
	'storage.pool.cancelScrub': 'Cancel scrub',
	'storage.pool.balance': 'Balance…',
	'storage.pool.addDisk': 'Add disk…',
	'storage.pool.removeDisk': 'Remove disk…',
	'storage.pool.pauseBalance': 'Pause',
	'storage.pool.resumeBalance': 'Resume',
	'storage.pool.cancelBalance': 'Cancel balance',
	'storage.pool.volumes': 'Volumes',
	'storage.pool.mount': 'Mount…',
	'storage.pool.unmount': 'Unmount…',
	// The confirmations of the Mount and Unmount dialogs: the action itself, without "…".
	'storage.mount.confirm': 'Mount',
	'storage.unmount.confirm': 'Unmount',
	'storage.pool.removeShort': 'Remove pool…',
	'storage.pool.blocked.readOnly': 'Balance and disk changes need the pool mounted read-write.',
	'storage.pool.blocked.missing': 'Disk changes wait until the missing disk is back.',
	'storage.pool.blocked.scrub': 'Balance and disk changes wait for the scrub to end.',
	'storage.pool.blocked.balance': 'This waits for the balance to end.',
	'storage.pool.blocked.balancePaused': 'Resume or cancel the paused balance first.',
	'storage.pool.blocked.removal': 'This waits for the disk removal to end.',
	// The fields of a pool's card, each before its value.
	'storage.pool.raid': 'RAID',
	'storage.pool.used': 'Used',
	'storage.pool.devices': 'Disks',
	'storage.pool.mountpoint': 'Mount point',
	'storage.pool.usedOf': '{used} of {size}',
	'storage.pool.capacityOf': 'Capacity of {name}',
	'storage.pool.unsafe': 'Unsafe',
	'storage.pool.unknown': 'unknown',
	'storage.pool.unknownNotMounted': 'unknown (not mounted)',
	'storage.pool.notMounted': 'Not mounted',
	'storage.pool.missingTitle': { one: '{n} disk is missing', other: '{n} disks are missing' },
	'storage.pool.missing': 'Reconnect or replace it before the remaining redundancy is lost.',
	'storage.pool.forcedReadOnlyTitle': 'Read-only after an error',
	'storage.pool.forcedReadOnly': 'Writes to the pool fail; the kernel log (dmesg) names the cause.',
	'storage.pool.readOnlyTitle': 'Mounted read-only',
	'storage.pool.readOnly': 'Shares and apps cannot write to it until it is mounted read-write again.',
	'storage.pool.deviceErrorsTitle': { one: '{n} disk error', other: '{n} disk errors' },
	// Before the command that resets the counters.
	'storage.pool.deviceErrors':
		"btrfs counted read, write or checksum errors on the pool's disks. Check the disks' SMART health and run a scrub.",
	'storage.pool.resetWith': 'The count stays until it is reset with',
	// The title of a pool's alert that lists more than one problem.
	'storage.pool.issuesTitle': { one: '{n} problem', other: '{n} problems' },
	'storage.pool.unreadableTitle': 'State unknown',
	'storage.pool.unreadable':
		"UNOS could not read the host's mount table or the pool's error counters; the system logs say why.",

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
	// A pool's balance, as btrfs reports it, and its dialog (ADR-0379).
	'storage.balance.statusError': 'Could not read the balance status',
	'storage.balance.running': 'Balance in progress',
	'storage.balance.pausing': 'Pausing after the chunk being moved',
	'storage.balance.cancelling': 'Cancelling the balance',
	'storage.balance.paused': 'Balance paused',
	'storage.balance.pausedNote':
		"A paused balance goes on when you resume it, and also when the pool is mounted again, as at the next start of the NAS; cancel it to stop it for good. When a paused full balance goes on, it rewrites only the chunks less than 90% full (the Linux kernel's rule): to rewrite every chunk, cancel it and start a new full balance.",
	'storage.balance.progress': 'Balance progress',
	'storage.balance.done': '{percent} done',
	'storage.balance.leftSeconds': 'Less than a minute left',
	'storage.balance.leftMinutes': 'About {minutes} min left',
	'storage.balance.leftHours': 'About {hours} h {minutes} min left',
	'storage.balance.finished': 'Last balance: finished',
	'storage.balance.cancelled': 'Last balance: cancelled before it finished',
	'storage.balance.failed': 'Last balance failed',
	'storage.balance.title': 'Balance {name}',
	'storage.balance.intro':
		'A balance rewrites chunks of the pool, the blocks of space that btrfs gives to data and metadata, and frees the space of partly empty ones for new chunks. The pool stays usable, but slower, while it runs.',
	'storage.balance.what': 'Chunks to rewrite',
	'storage.balance.usageOption': 'Only chunks less full than a limit (recommended)',
	'storage.balance.fullOption': 'Every chunk (full balance)',
	'storage.balance.usage': 'Usage limit (%)',
	'storage.balance.usageHint':
		'The data and metadata chunks less full than this are rewritten. A low limit frees the most space for the least work: minutes, not hours.',
	'storage.balance.usageInvalid': 'Enter a whole number from 1 to 99',
	'storage.balance.fullTitle': 'A full balance takes long',
	'storage.balance.fullWarning':
		'It rewrites every chunk, all the data of the pool: hours on a hard disk, with the pool slower meanwhile. It is rarely needed, such as after a disk was added to the pool. If it is paused, or the NAS stops before it ends, it then rewrites only the chunks less than 90% full: to rewrite every chunk, cancel it and start a new one.',
	'storage.balance.fullConfirm': 'Rewrite every chunk of {name}',
	'storage.balance.start': 'Start balance',
	'storage.balance.startFailed': 'The balance did not start',

	// A disk added to a pool or removed from one, and the removal on the pool card (ADR-0411).
	'storage.addDisk.title': 'Add a disk to {name}',
	'storage.addDisk.intro':
		'The disk joins the pool, and the pool grows by its size at once. Everything on the disk is erased. The pool keeps its profile ({profiles}): new data is spread over the new disk too, and a full balance spreads the data that is there already.',
	'storage.addDisk.loadError': 'Could not list the disks',
	'storage.addDisk.noneTitle': 'No disk can be added',
	'storage.addDisk.none':
		'A disk can be added when it is not the system disk, holds no other pool or btrfs filesystem, and is not mounted or in use. Connect such a disk, then open this dialog again.',
	'storage.addDisk.disk': 'Disk to add',
	'storage.addDisk.hasData': 'Contains data',
	'storage.addDisk.dataTitle': 'This disk is not empty',
	'storage.addDisk.data': 'Adding it erases what it holds.',
	'storage.addDisk.erase': 'Erase everything on {path}',
	'storage.addDisk.confirm': "Type the disk's path to confirm",
	'storage.addDisk.mismatch': "This is not the disk's path",
	'storage.addDisk.submit': 'Add disk',
	'storage.addDisk.failed': 'The disk was not added',
	'storage.removeDisk.title': 'Remove a disk from {name}',
	'storage.removeDisk.intro':
		"The disk's data moves to the other disks of the pool first, which can take hours on a hard disk; the pool stays usable meanwhile, and the disk leaves it at the end. A removal that the pool's profile or the space of the other disks cannot survive is refused.",
	'storage.removeDisk.loadError': 'Could not read the disks of the pool',
	'storage.removeDisk.disk': 'Disk to remove',
	'storage.removeDisk.used': '{used} of data on it',
	'storage.removeDisk.cannot': 'Cannot be removed',
	'storage.removeDisk.confirm': "Type the disk's path to confirm",
	'storage.removeDisk.mismatch': "This is not the disk's path",
	'storage.removeDisk.submit': 'Remove disk',
	'storage.removeDisk.failed': 'The removal did not start',
	'storage.removal.statusError': 'Could not read the disk removal',
	'storage.removal.running': 'Removing {device}: its data moves to the other disks',
	'storage.removal.progress': 'Removal progress',
	'storage.removal.left': '{left} left to move of {total}',
	'storage.removal.leftOnly': '{left} left to move',
	'storage.removal.finished': 'Last disk removal: {device} left the pool',
	'storage.removal.failed': 'The removal of {device} failed',

	'storage.create.title': 'Create a pool',
	'storage.create.eraseTitle': 'This erases the disks you choose',
	'storage.create.erase': 'They are formatted with btrfs: everything on them is lost.',
	'storage.create.name': 'Pool name',
	// An example of a pool's name.
	'storage.create.namePlaceholder': 'data',
	'storage.create.raidLevel': 'RAID level',
	'storage.create.devices': 'Disks',
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
	'storage.create.confirmName': 'Type {name} to confirm',
	'storage.create.noneUsable': 'No disk can hold a new pool.',
	'storage.create.unusable': { one: '{n} disk not available', other: '{n} disks not available' },
	'storage.create.missing': 'To create the pool: {list}.',
	'storage.create.needName': 'enter a name',
	'storage.create.needDisks': { one: 'choose at least {n} disk', other: 'choose at least {n} disks' },
	'storage.create.needErase': 'agree to erase their data',
	'storage.create.needConfirm': 'type the name again',
	'storage.create.mismatch': 'Does not match the pool name',
	'storage.create.failed': 'Pool was not created',
	'storage.create.cleared': 'The selection has been cleared. Close this dialog and open it again to reload the disks.',
	'storage.create.reloaded':
		'The disk list has been reloaded and the selection cleared. Check the disks and select them again.',
	'storage.create.submit': 'Erase disks and create pool',

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

	'storage.mount.title': 'Mount pool',
	// The question of the dialog, around the pool's name and its mountpoint: "Mount "<name>" at <path>?"
	'storage.mount.question': 'Mount',
	'storage.mount.at': 'at',
	'storage.mount.kept':
		'The disks are not formatted: the data on them is kept. UNOS also adds an /etc/fstab entry so the pool is mounted again after a reboot.',
	'storage.mount.failed': 'Mount failed',
	'storage.unmount.title': 'Unmount pool',
	// The question of the dialog, before the pool's name: "Unmount "<name>"?"
	'storage.unmount.question': 'Unmount',
	// Around the pool's mountpoint.
	'storage.unmount.before': 'Shares, apps and scheduled tasks that use',
	'storage.unmount.after':
		'lose access until the pool is mounted again. The /etc/fstab entry is removed, so the pool is not mounted at boot. The data on the disks is not touched.',
	'storage.unmount.failed': 'Unmount failed',

	'storage.import.title': 'Import pool',
	'storage.import.confirm': 'Import',
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
	'storage.import.keptAfter': 'by its UUID and adds an /etc/fstab entry, so the pool is mounted again after a reboot.',
	// The last part of the mountpoint while no name is entered.
	'storage.import.nameSlot': '<name>',
	'storage.import.name': 'Pool name',
	// An example of a pool's name.
	'storage.import.namePlaceholder': 'backup',
	'storage.import.nameTaken': 'A pool named "{name}" exists already. Choose another name.',
	'storage.import.labelTaken': 'Another btrfs filesystem found on the disks is labelled "{name}". Choose another name.',
	'storage.import.failed': 'Pool was not imported',

	'storage.remove.title': 'Remove pool',
	// Before the pool's name.
	'storage.remove.about': 'UNOS stops using the pool',
	'storage.remove.aboutEnd': '.',
	'storage.remove.unmounted': 'It is unmounted, and no longer mounted at boot:',
	'storage.remove.kept':
		'Its data ({used}) stays on the disks, which are not wiped: the pool can be mounted again later.',
	'storage.remove.users': 'Shares and apps that use it stop working.',
	'storage.remove.confirm': 'Type {name} to confirm',
	'storage.remove.failed': 'Pool was not removed',
	'storage.remove.submit': 'Remove pool',

	// A pool's volumes: btrfs subvolumes in its top folder (ADR-0243).
	'storage.volumes.title': 'Volumes on {pool}',
	'storage.volumes.titleNone': 'Volumes',
	// Around the pool's mountpoint.
	'storage.volumes.introBefore': 'A volume is a folder of the pool in',
	'storage.volumes.introAfter':
		'with snapshots and a quota of its own, which can be shared on its own. A snapshot of the pool leaves it out, and scheduled snapshots take pools only.',
	'storage.volumes.readOnly': 'Volumes cannot be created or deleted while the pool is mounted read-only.',
	'storage.volumes.forcedReadOnly': 'btrfs made it read-only after an error; check the kernel log (dmesg).',
	'storage.volumes.loadError': 'Could not load the volumes',
	'storage.volumes.none': 'No volumes yet',
	'storage.volumes.deleteOne': 'Delete volume {name}',
	'storage.volumes.deleteTitle': 'Delete volume "{name}"?',
	// Around the volume's path.
	'storage.volumes.deleteBefore': 'All files in',
	'storage.volumes.deleteAfter':
		'are deleted with it, including those of stopped apps. This cannot be undone. While a running app or container, an SMB share or an NFS export uses the volume, it is not deleted.',
	'storage.volumes.confirm': 'Type {name} to confirm',
	'storage.volumes.deleteFailed': 'Volume was not deleted',
	'storage.volumes.delete': 'Delete volume',
	'storage.volumes.name': 'New volume name',
	// An example of a volume's name.
	'storage.volumes.namePlaceholder': 'documents',
	'storage.volumes.quota': 'Quota in GiB (optional)',
	'storage.volumes.quotaPlaceholder': 'No limit',
	// The field takes a number with a decimal point, in every language.
	'storage.volumes.quotaError': 'Enter a number of GiB greater than 0, such as 10 or 0.5 (at most {max})',
	'storage.volumes.quotaHint': 'Empty for no limit. A quota needs quotas enabled on the pool.',
	'storage.volumes.createFailed': 'Volume was not created',
	'storage.volumes.create': 'Create volume',
	'storage.volumes.close': 'Close',
	// A volume's use of its quota, as the kernel counts it: {used} and {quota} are sizes,
	// {percent} a percentage, in the format of the page.
	'storage.volumes.useOf': 'Use of {name}',
	'storage.volumes.usedOfQuota': '{used} of {quota} used ({percent})',
	'storage.volumes.usedNoQuota': '{used} used, no quota',
	'storage.volumes.useUnknown': 'Use unknown: its quota group cannot be read',
	// Followed by the command that turns quotas on, or counts them again, on the host.
	'storage.volumes.quotasOffTitle': 'Quotas are off on this pool',
	'storage.volumes.quotasOff':
		"The volumes' use is not counted. Quotas count it and limit volumes, but they slow down deleting snapshots and balancing the pool, the more so the more snapshots it has. To enable them, run on the host:",
	'storage.volumes.rescanTitle': 'The counts need a rescan',
	'storage.volumes.rescan':
		'btrfs says that the use counted on this pool may be wrong until it is counted again. Run on the host:',
	// The use over time: one point per hour of the last 30 days, the current reading last.
	'storage.volumes.chartShow': 'Use over time of {name}',
	'storage.volumes.chartTitle': 'Use over time',
	'storage.volumes.chartError': 'Could not load the use over time',
	'storage.volumes.chartTooFew': 'Not enough points yet: UNOS keeps one per hour, for 30 days.',
	// {from} and {to} are dates and times; {start} and {end} the sizes used then.
	'storage.volumes.chartSummary': '{start} used on {from}, {end} on {to}.',
	'storage.volumes.chartQuota': 'Quota: {quota}.'
};

export const ru: Translation<typeof en> = {
	'storage.title': 'Хранилище',
	'storage.createPool': 'Создать пул',
	'storage.createBlocked': 'Не удалось прочитать диски: нажмите «Обновить», чтобы попробовать снова.',

	'storage.size.bytes': '{size} Б',
	'storage.size.kb': '{size} КБ',
	'storage.size.mb': '{size} МБ',
	'storage.size.gb': '{size} ГБ',
	'storage.size.tb': '{size} ТБ',
	'storage.quoted': '«{name}»',
	'storage.nameRule':
		'Используйте латинские буквы, цифры, «-» и «_» (до 63 символов), начиная с латинской буквы или цифры',

	'storage.disks.title': 'Диски',
	'storage.disks.loadError': 'Не удалось загрузить диски',
	'storage.disks.none': 'Диски не найдены',
	'storage.disks.column.device': 'Устройство',
	'storage.disks.column.model': 'Модель',
	'storage.disks.column.size': 'Размер',
	'storage.disks.column.type': 'Тип',
	'storage.disks.column.contents': 'Содержимое',
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
	'storage.disks.idleTimeout': 'Тайм-аут простоя',

	'storage.spindown.off': 'Выкл.',
	'storage.spindown.minutes': { one: '{n} минута', few: '{n} минуты', many: '{n} минут' },
	'storage.spindown.hours': { one: '{n} час', few: '{n} часа', many: '{n} часов' },
	'storage.spindown.of': 'Тайм-аут простоя {disk}:',
	'storage.spindown.title': 'Тайм-аут простоя',
	'storage.spindown.disk': 'Диск останавливается после такого времени без обращений:',
	'storage.spindown.label': 'Остановить через',
	'storage.spindown.hint':
		'Сохранение сейчас раскрутит диск. Диск, который часто будят, изнашивается быстрее: для диска, которым пользуются каждый день, выберите время подольше.',
	'storage.spindown.scans':
		'Пока у диска есть тайм-аут, UNOS выключает его собственное автоматическое офлайн-сканирование (проверку поверхности, которую диск сам запускает каждые несколько часов и во время которой не засыпает) и включает его снова, когда тайм-аут убирают. Чтобы вместо этого читать его данные, запланируйте проверку целостности его пула (Система, Планировщик); всю поверхность читает длинный тест SMART, но тесты SMART Планировщика короткие.',
	'storage.spindown.failed': 'Тайм-аут простоя не установлен',
	'storage.spindown.saved': 'Тайм-аут простоя сохранён',
	'storage.spindown.save': 'Сохранить',

	'storage.found.title': 'Найдено на дисках',
	'storage.found.searchError': 'Не удалось выполнить поиск пулов для импорта',
	'storage.found.intro':
		'Файловые системы btrfs на дисках хоста, которые ещё не стали пулами. Импорт сохраняет их данные.',
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
	'storage.found.import': 'Импортировать…',
	'storage.found.importOne': 'файловую систему {uuid}',

	'storage.pools.title': 'Пулы',
	'storage.pools.unsafeTitle': 'RAID 5 и 6 в btrfs ненадёжны',
	'storage.pools.unsafe':
		'Сбой питания может повредить данные. Сделайте резервную копию и создайте пул заново как RAID 1, 1C3 или 1C4.',
	'storage.pools.loadError': 'Не удалось загрузить пулы',
	'storage.pools.none': 'Пулов пока нет',
	'storage.pools.noneHint': 'Кнопка «Создать пул» соберёт его из свободных дисков.',
	'storage.pools.freeDisks': {
		one: 'Для него свободен {n} диск: его соберёт кнопка «Создать пул».',
		few: 'Для него свободны {n} диска: его соберёт кнопка «Создать пул».',
		many: 'Для него свободны {n} дисков: его соберёт кнопка «Создать пул».'
	},

	'storage.health.healthy': 'Исправен',
	'storage.health.degraded': 'Деградирован',
	'storage.health.unmounted': 'Не смонтирован',
	'storage.health.error': 'Ошибка',
	'storage.health.unknown': 'Неизвестно',

	'storage.profile.single': 'Single (без избыточности)',
	'storage.profile.dup': 'DUP (2 копии на одном диске)',
	'storage.profile.raid0': 'RAID 0 (чередование)',
	'storage.profile.raid1': 'RAID 1 (2 копии)',
	'storage.profile.raid1c3': 'RAID 1C3 (3 копии)',
	'storage.profile.raid1c4': 'RAID 1C4 (4 копии)',
	'storage.profile.raid10': 'RAID 10 (чередование зеркал)',
	'storage.profile.raid5': 'RAID 5',
	'storage.profile.raid6': 'RAID 6',

	'storage.pool.notMountedAtBoot': 'Не монтируется при загрузке',
	'storage.pool.mountedAtBoot': 'Монтируется при загрузке',
	'storage.pool.inFstab': 'Есть в fstab, но не смонтирован',
	'storage.pool.scrub': 'Проверить целостность',
	'storage.pool.cancelScrub': 'Отменить проверку',
	'storage.pool.balance': 'Балансировка…',
	'storage.pool.addDisk': 'Добавить диск…',
	'storage.pool.removeDisk': 'Удалить диск…',
	'storage.pool.pauseBalance': 'Приостановить',
	'storage.pool.resumeBalance': 'Продолжить',
	'storage.pool.cancelBalance': 'Отменить балансировку',
	'storage.pool.volumes': 'Тома',
	'storage.pool.mount': 'Смонтировать…',
	'storage.pool.unmount': 'Отмонтировать…',
	'storage.mount.confirm': 'Смонтировать',
	'storage.unmount.confirm': 'Отмонтировать',
	'storage.pool.removeShort': 'Убрать пул…',
	'storage.pool.blocked.readOnly': 'Балансировке и смене дисков нужен пул, смонтированный для записи.',
	'storage.pool.blocked.missing': 'Смена дисков ждёт, пока недостающий диск не вернётся.',
	'storage.pool.blocked.scrub': 'Балансировка и смена дисков ждут окончания проверки целостности.',
	'storage.pool.blocked.balance': 'Это ждёт окончания балансировки.',
	'storage.pool.blocked.balancePaused': 'Сначала продолжите или отмените приостановленную балансировку.',
	'storage.pool.blocked.removal': 'Это ждёт окончания удаления диска.',
	'storage.pool.raid': 'RAID',
	'storage.pool.used': 'Занято',
	'storage.pool.devices': 'Диски',
	'storage.pool.mountpoint': 'Точка монтирования',
	'storage.pool.usedOf': '{used} из {size}',
	'storage.pool.capacityOf': 'Заполнение пула {name}',
	'storage.pool.unsafe': 'Небезопасно',
	'storage.pool.unknown': 'неизвестно',
	'storage.pool.unknownNotMounted': 'неизвестно (не смонтирован)',
	'storage.pool.notMounted': 'Не смонтирован',
	'storage.pool.missingTitle': {
		one: 'Не хватает {n} диска',
		few: 'Не хватает {n} дисков',
		many: 'Не хватает {n} дисков'
	},
	'storage.pool.missing': 'Подключите или замените его, пока не потеряна оставшаяся избыточность.',
	'storage.pool.forcedReadOnlyTitle': 'Только для чтения после ошибки',
	'storage.pool.forcedReadOnly': 'Запись в пул не удаётся; причину называет журнал ядра (dmesg).',
	'storage.pool.readOnlyTitle': 'Смонтирован только для чтения',
	'storage.pool.readOnly': 'Общие папки и приложения не могут писать в него, пока он снова не смонтирован для записи.',
	'storage.pool.deviceErrorsTitle': { one: '{n} ошибка дисков', few: '{n} ошибки дисков', many: '{n} ошибок дисков' },
	'storage.pool.deviceErrors':
		'btrfs насчитал ошибки чтения, записи или контрольных сумм на дисках пула. Проверьте состояние SMART дисков и запустите проверку целостности.',
	'storage.pool.resetWith': 'Счётчик сохраняется, пока его не сбросят командой',
	'storage.pool.issuesTitle': { one: '{n} проблема', few: '{n} проблемы', many: '{n} проблем' },
	'storage.pool.unreadableTitle': 'Состояние неизвестно',
	'storage.pool.unreadable':
		'UNOS не удалось прочитать таблицу монтирования хоста или счётчики ошибок пула; причину покажут системные журналы.',

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
	'storage.balance.statusError': 'Не удалось прочитать состояние балансировки',
	'storage.balance.running': 'Идёт балансировка',
	'storage.balance.pausing': 'Приостанавливается после перемещаемого блока',
	'storage.balance.cancelling': 'Балансировка отменяется',
	'storage.balance.paused': 'Балансировка приостановлена',
	'storage.balance.pausedNote':
		'Приостановленная балансировка продолжится, когда вы её возобновите, а также при следующем монтировании пула, например при следующем запуске NAS; чтобы остановить её совсем, отмените её. Когда приостановленная полная балансировка продолжится, она перезапишет только блоки, заполненные меньше чем на 90% (правило ядра Linux): чтобы перезаписать все блоки, отмените её и начните новую полную балансировку.',
	'storage.balance.progress': 'Ход балансировки',
	'storage.balance.done': 'Выполнено {percent}',
	'storage.balance.leftSeconds': 'Осталось меньше минуты',
	'storage.balance.leftMinutes': 'Осталось около {minutes} мин',
	'storage.balance.leftHours': 'Осталось около {hours} ч {minutes} мин',
	'storage.balance.finished': 'Последняя балансировка: завершена',
	'storage.balance.cancelled': 'Последняя балансировка: отменена до завершения',
	'storage.balance.failed': 'Последняя балансировка не удалась',
	'storage.balance.title': 'Балансировка {name}',
	'storage.balance.intro':
		'Балансировка перезаписывает блоки пула — участки места, которые btrfs выделяет под данные и метаданные, — и освобождает место частично пустых блоков под новые. Пока она идёт, пул работает, но медленнее.',
	'storage.balance.what': 'Какие блоки перезаписать',
	'storage.balance.usageOption': 'Только заполненные меньше порога (рекомендуется)',
	'storage.balance.fullOption': 'Все блоки (полная балансировка)',
	'storage.balance.usage': 'Порог заполнения (%)',
	'storage.balance.usageHint':
		'Перезаписываются блоки данных и метаданных, заполненные меньше порога. Низкий порог освобождает больше всего места за меньшую работу: минуты, а не часы.',
	'storage.balance.usageInvalid': 'Введите целое число от 1 до 99',
	'storage.balance.fullTitle': 'Полная балансировка идёт долго',
	'storage.balance.fullWarning':
		'Она перезаписывает все блоки, все данные пула: на жёстком диске это часы, и всё это время пул работает медленнее. Она нужна редко, например после добавления диска в пул. Если она будет приостановлена или работа NAS прервётся до её завершения, дальше она перезапишет только блоки, заполненные меньше чем на 90%: чтобы перезаписать все блоки, отмените её и начните новую.',
	'storage.balance.fullConfirm': 'Перезаписать все блоки {name}',
	'storage.balance.start': 'Начать балансировку',
	'storage.balance.startFailed': 'Балансировка не началась',

	'storage.addDisk.title': 'Добавить диск в {name}',
	'storage.addDisk.intro':
		'Диск войдёт в пул, и пул сразу вырастет на его размер. Всё, что есть на диске, будет стёрто. Профиль пула ({profiles}) сохранится: новые данные лягут и на новый диск, а полная балансировка распределит уже записанные.',
	'storage.addDisk.loadError': 'Не удалось получить список дисков',
	'storage.addDisk.noneTitle': 'Нет диска, который можно добавить',
	'storage.addDisk.none':
		'Добавить можно диск, который не системный, не содержит другого пула или файловой системы btrfs, не смонтирован и не занят. Подключите такой диск и откройте это окно снова.',
	'storage.addDisk.disk': 'Добавляемый диск',
	'storage.addDisk.hasData': 'Содержит данные',
	'storage.addDisk.dataTitle': 'Диск не пуст',
	'storage.addDisk.data': 'При добавлении всё, что на нём есть, будет стёрто.',
	'storage.addDisk.erase': 'Стереть всё на {path}',
	'storage.addDisk.confirm': 'Введите путь диска для подтверждения',
	'storage.addDisk.mismatch': 'Это не путь диска',
	'storage.addDisk.submit': 'Добавить диск',
	'storage.addDisk.failed': 'Диск не добавлен',
	'storage.removeDisk.title': 'Удалить диск из {name}',
	'storage.removeDisk.intro':
		'Сначала данные диска переносятся на другие диски пула, на жёстком диске это может занять часы; пул всё это время работает, а диск покидает его в конце. Удаление, которого не выдержит профиль пула или место на других дисках, отклоняется.',
	'storage.removeDisk.loadError': 'Не удалось прочитать диски пула',
	'storage.removeDisk.disk': 'Удаляемый диск',
	'storage.removeDisk.used': 'Данных на нём: {used}',
	'storage.removeDisk.cannot': 'Удалить нельзя',
	'storage.removeDisk.confirm': 'Введите путь диска для подтверждения',
	'storage.removeDisk.mismatch': 'Это не путь диска',
	'storage.removeDisk.submit': 'Удалить диск',
	'storage.removeDisk.failed': 'Удаление не началось',
	'storage.removal.statusError': 'Не удалось прочитать состояние удаления диска',
	'storage.removal.running': 'Удаляется {device}: его данные переносятся на другие диски',
	'storage.removal.progress': 'Ход удаления',
	'storage.removal.left': 'Осталось перенести {left} из {total}',
	'storage.removal.leftOnly': 'Осталось перенести {left}',
	'storage.removal.finished': 'Последнее удаление диска: {device} покинул пул',
	'storage.removal.failed': 'Удаление {device} не удалось',

	'storage.create.title': 'Новый пул',
	'storage.create.eraseTitle': 'Выбранные диски будут стёрты',
	'storage.create.erase': 'Они форматируются в btrfs: всё, что на них есть, будет потеряно.',
	'storage.create.name': 'Имя пула',
	'storage.create.namePlaceholder': 'data',
	'storage.create.raidLevel': 'Уровень RAID',
	'storage.create.devices': 'Диски',
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
	'storage.create.confirmName': 'Введите {name} для подтверждения',
	'storage.create.noneUsable': 'Ни один диск не подходит для нового пула.',
	'storage.create.unusable': { one: '{n} диск недоступен', few: '{n} диска недоступны', many: '{n} дисков недоступны' },
	'storage.create.missing': 'Чтобы создать пул: {list}.',
	'storage.create.needName': 'введите имя',
	'storage.create.needDisks': {
		one: 'выберите хотя бы {n} диск',
		few: 'выберите хотя бы {n} диска',
		many: 'выберите хотя бы {n} дисков'
	},
	'storage.create.needErase': 'согласитесь стереть их данные',
	'storage.create.needConfirm': 'введите имя ещё раз',
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
	'storage.import.confirm': 'Импортировать',
	'storage.import.filesystem': 'Импортировать файловую систему btrfs',
	'storage.import.noLabel': 'без метки',
	'storage.import.label': 'метка «{label}»',
	'storage.import.on': 'на',
	'storage.import.asPool': 'как пул.',
	'storage.import.keptTitle': 'Диски не форматируются',
	'storage.import.keptBefore':
		'Данные на них сохраняются. UNOS сделает имя пула меткой файловой системы, смонтирует её в',
	'storage.import.keptAfter': 'по UUID и добавит запись в /etc/fstab, чтобы пул снова монтировался после перезагрузки.',
	'storage.import.nameSlot': '<имя>',
	'storage.import.name': 'Имя пула',
	'storage.import.namePlaceholder': 'backup',
	'storage.import.nameTaken': 'Пул «{name}» уже есть. Выберите другое имя.',
	'storage.import.labelTaken':
		'У другой файловой системы btrfs на дисках уже есть метка «{name}». Выберите другое имя.',
	'storage.import.failed': 'Не удалось импортировать пул',

	'storage.remove.title': 'Убрать пул',
	'storage.remove.about': 'UNOS перестаёт использовать пул',
	'storage.remove.aboutEnd': '.',
	'storage.remove.unmounted': 'Он отмонтируется и больше не монтируется при загрузке:',
	'storage.remove.kept':
		'Его данные ({used}) остаются на дисках, диски не стираются: пул можно будет снова смонтировать.',
	'storage.remove.users': 'Общие папки и приложения, которые его используют, перестанут работать.',
	'storage.remove.confirm': 'Введите {name} для подтверждения',
	'storage.remove.failed': 'Не удалось убрать пул',
	'storage.remove.submit': 'Убрать пул',

	'storage.volumes.title': 'Тома в пуле {pool}',
	'storage.volumes.titleNone': 'Тома',
	'storage.volumes.introBefore': 'Том — это папка пула в',
	'storage.volumes.introAfter':
		'со своими снимками и квотой; её можно открыть в сеть отдельно. Снимок пула её не включает, а снимки по расписанию делаются только для пулов.',
	'storage.volumes.readOnly': 'Тома нельзя создавать и удалять, пока пул смонтирован только для чтения.',
	'storage.volumes.forcedReadOnly':
		'btrfs перевёл его в режим только для чтения после ошибки; проверьте журнал ядра (dmesg).',
	'storage.volumes.loadError': 'Не удалось загрузить тома',
	'storage.volumes.none': 'Томов пока нет',
	'storage.volumes.deleteOne': 'Удалить том {name}',
	'storage.volumes.deleteTitle': 'Удалить том «{name}»?',
	'storage.volumes.deleteBefore': 'Все файлы в',
	'storage.volumes.deleteAfter':
		'будут удалены вместе с ним, включая файлы остановленных приложений. Это нельзя отменить. Пока том используется работающим приложением или контейнером, общей папкой SMB или экспортом NFS, он не удаляется.',
	'storage.volumes.confirm': 'Введите {name} для подтверждения',
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
	'storage.volumes.close': 'Закрыть',
	'storage.volumes.useOf': 'Заполнение тома {name}',
	'storage.volumes.usedOfQuota': 'Занято {used} из {quota} ({percent})',
	'storage.volumes.usedNoQuota': 'Занято {used}, квоты нет',
	'storage.volumes.useUnknown': 'Заполнение неизвестно: группу квоты тома не удаётся прочитать',
	'storage.volumes.quotasOffTitle': 'В этом пуле квоты выключены',
	'storage.volumes.quotasOff':
		'Заполнение томов не считается. Квоты считают его и ограничивают тома, но замедляют удаление снимков и балансировку пула, тем сильнее, чем больше в нём снимков. Чтобы включить их, выполните на хосте:',
	'storage.volumes.rescanTitle': 'Счётчики нужно пересчитать',
	'storage.volumes.rescan':
		'btrfs сообщает, что заполнение в этом пуле может быть посчитано неверно, пока его не пересчитают. Выполните на хосте:',
	'storage.volumes.chartShow': 'Заполнение тома {name} во времени',
	'storage.volumes.chartTitle': 'Заполнение во времени',
	'storage.volumes.chartError': 'Не удалось загрузить заполнение во времени',
	'storage.volumes.chartTooFew': 'Пока мало точек: UNOS хранит по одной в час за 30 дней.',
	'storage.volumes.chartSummary': 'Занято {start} на {from} и {end} на {to}.',
	'storage.volumes.chartQuota': 'Квота: {quota}.'
};
