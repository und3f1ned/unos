import type { Translation } from '../translate';

// The Shares page: src/routes/shares/+page.svelte with its SMB and NFS tabs, and the components of
// src/lib/components/shares/ (the share form, the access picker, a share's card, Repair
// permissions, the NFS export form, and the SMB state badge that the Users page shows too). The
// server's messages, a share's import notes and the folder problems it answers are shown as they
// come, in English; the texts marked "as the server words them" are the page's own copies of its
// answers, shown before anything is sent.

export const en = {
	'shares.title': 'Shares',
	'shares.tabs.label': 'Share types',
	'shares.pathsError': 'The pools and volumes could not be read',
	'shares.noPools': 'No storage pools available. Create a storage pool first before adding shares.',

	// The access levels (ShareAccessLevel), and none.
	'shares.level.none': 'No access',
	'shares.level.read': 'Read only',
	'shares.level.write': 'Read & write',

	// The locations of the share form and the export form (GET /shares/paths): a pool's name, or
	// pool/volume, and its kind.
	'shares.location.label': 'Location',
	'shares.location.choose': 'Choose a pool or volume',
	'shares.location.pool': '{name} (Pool)',
	'shares.location.volume': '{name} (Volume)',
	'shares.location.noneMounted': 'No pool or volume is mounted. Mount a pool on the Storage page first.',
	'shares.location.noneManaged': 'No pool that UNOS manages is mounted.',
	'shares.location.folder': 'Folder',

	'shares.smb.add': 'Add SMB share',
	'shares.smb.title': 'SMB shares',
	'shares.smb.loadError': 'The shares could not be loaded',
	'shares.smb.empty': 'No SMB shares yet',
	'shares.smb.emptyHint': 'Share a folder of a pool, or a volume, with the users you choose.',
	// The networks that smb.conf answers (HOSTS_ALLOW of supervisor/src/shares/smbconf.py, ADR-0095).
	'shares.smb.networkNote':
		'SMB answers only addresses of your local network (private IPv4, IPv6 ULA and link-local, VPN ranges such as 100.64.0.0/10); public addresses are refused.',
	'shares.smb.created': 'Share "{name}" created',
	'shares.smb.saved': 'Share "{name}" saved',
	'shares.smb.deleted': 'Share "{name}" deleted',
	'shares.smb.deleteTitle': 'Delete share',
	// The question of the dialog, in parts around the share's name (ADR-0296): "Delete share <name>? The
	// folder and its files are kept. ..."
	'shares.smb.deleteBefore': 'Delete share',
	'shares.smb.deleteAfter': '? The folder and its files are kept. Open connections to the share are closed.',
	'shares.smb.notDeleted': 'Share was not deleted',
	'shares.smb.delete': 'Delete share',

	// GET /shares/smb-status.
	'shares.status.error': 'The SMB status could not be read',
	'shares.status.sambaMissing': 'Samba is not installed on the host: shares and SMB accounts cannot be used',
	'shares.status.missing': 'Missing: {tools}',
	'shares.status.guestShares':
		'Guest access is on for: {shares}. Anyone on your network can read these shares without a password, and connections to this server are not required to be signed.',
	'shares.status.imported': 'Shares imported from the previous smb.conf',
	'shares.status.unmanagedUsers':
		'These Samba accounts were not created by UNOS and cannot use UNOS shares: {users}',
	'shares.status.nobody': 'Nobody can connect yet: allow SMB on the Users page, then grant access here.',

	'shares.nfs.add': 'Add NFS export',
	'shares.nfs.title': 'NFS exports',
	'shares.nfs.serverMissing': 'The NFS server is not installed on the host: no computer can mount the exports',
	'shares.nfs.installServer': 'Install the package nfs-kernel-server on the NAS.',
	'shares.nfs.statusError': 'The NFS status could not be read',
	'shares.nfs.loadError': 'The NFS exports could not be loaded',
	'shares.nfs.empty': 'No NFS exports yet',
	'shares.nfs.emptyHint': 'Export a folder of a pool, or a volume, to the computers of a network.',
	'shares.nfs.clientsOf': 'Clients of {path}',
	// A client whose options differ by security flavor (sec=): neither read only nor read & write.
	'shares.nfs.perFlavor': 'Per security flavor',
	'shares.nfs.notAddedInUnos': 'In {file}, line {line}: not added in UNOS, so change it on the NAS',
	'shares.nfs.deleteOne': 'Delete export {path}',
	// What an NFS client may do: NFS checks no password (supervisor/src/shares/nfs.py, ADR-0293).
	'shares.nfs.note':
		'NFS asks for no password: every computer in the client network can mount the export, and its users get the access that the user and group ids of their own accounts have in the folder (root on a client counts as nobody). UNOS exports only to addresses of your local network.',
	'shares.nfs.created': 'NFS export of {path} created',
	'shares.nfs.deleted': 'NFS export of {path} deleted',
	'shares.nfs.deleteTitle': 'Delete NFS export',
	// The question of the dialog, in parts around the folder's path (ADR-0296): "Delete the export of
	// <folder>? NFS clients lose access to it. ..."
	'shares.nfs.deleteBefore': 'Delete the export of',
	'shares.nfs.deleteAfter': '? NFS clients lose access to it. The folder and its files are kept.',
	'shares.nfs.notDeleted': 'Export was not deleted',
	'shares.nfs.delete': 'Delete export',

	// The form of a new NFS export: src/lib/components/shares/NfsExportFormModal.svelte.
	'shares.nfs.form.title': 'Add NFS export',
	'shares.nfs.form.folderPlaceholder': 'media',
	'shares.nfs.form.volumeHint': 'An existing folder inside the volume. Leave empty to export the whole volume',
	'shares.nfs.form.poolHint': 'An existing folder inside the pool, for example one made in Files',
	// Before the folder's path.
	'shares.nfs.form.exportedFolder': 'Exported folder:',
	'shares.nfs.form.client': 'Client network',
	'shares.nfs.form.clientHint':
		'One address or network of your local network, such as 192.168.1.0/24, 192.168.1.20 or fd00::/64',
	'shares.nfs.form.clientRequired': 'Enter an address or network',
	'shares.nfs.form.notCreated': 'Export was not created',
	'shares.nfs.form.create': 'Create export',
	'shares.nfs.form.unmanagedPools':
		'Pools marked "Not mounted at boot" on the Storage page are not listed: NFS exports need a pool that UNOS manages.',
	// A pool's top level and <pool>/apps, as the server words them (supervisor/src/shares/nfs.py).
	'shares.nfs.form.poolRoot':
		'Export a folder inside the pool or a volume: the top level of a pool holds app data (apps/), volumes and snapshots',
	'shares.nfs.form.appsFolder': 'App data ({pool}/apps) cannot be exported: apps keep their configuration there',

	// The form of a share: src/lib/components/shares/ShareFormModal.svelte.
	'shares.form.addTitle': 'Add SMB share',
	'shares.form.editTitle': 'Edit share {name}',
	'shares.form.name': 'Name',
	'shares.form.namePlaceholder': 'documents',
	'shares.form.nameRequired': 'Enter a name for the share',
	'shares.form.notServed': 'This share is not served',
	'shares.form.cannotBeShared': 'Its folder cannot be shared.',
	// In parts around the path of the folder it has (ADR-0296): "Folder now: <path>. Choose a folder that
	// can be shared."
	'shares.form.currentFolderBefore': 'Folder now:',
	'shares.form.currentFolderAfter': '. Choose a folder that can be shared.',
	// A share that is not served is saved only once it grants nothing, or with a folder that can be shared.
	'shares.form.notServedGrants':
		'Its access entries and guest access do not count while it is not served. To save other changes without a new folder, remove them: No access for every user, and guest access off.',
	// Shown when the location list leaves out pools that UNOS does not manage.
	'shares.form.unmanagedPools':
		'Pools marked "Not mounted at boot" on the Storage page are not listed: SMB shares need a pool that UNOS manages.',
	'shares.form.folderPlaceholder': 'documents',
	'shares.form.volumeHint':
		'A folder inside the pool or volume; created if missing. Leave empty to share the whole volume',
	'shares.form.poolHint': 'A folder inside the pool or volume; created if missing.',
	// Before the folder's path.
	'shares.form.sharedFolder': 'Shared folder:',
	// A pool's top level and <pool>/apps, as the server words them (supervisor/src/shares/service.py).
	'shares.form.poolRoot':
		'Share a folder inside the pool or a volume: the top level of a pool holds app data (apps/), volumes and snapshots',
	'shares.form.appsFolder': 'App data ({pool}/apps) cannot be shared: apps keep their configuration there',
	'shares.form.appsNote':
		'Folders that apps use (downloads, media) can be shared. SMB users with write access then change what the app sees.',
	'shares.form.comment': 'Comment',
	'shares.form.browseable': 'Show in network browsing',
	'shares.form.guest': 'Guests can read this share without a password',
	// As the server words its warning about a guest share (ADR-0096).
	'shares.form.guestWarning':
		'Anyone on your network can read this share without a password, and its name is visible to anyone on your network. Guests can never write. While a guest share exists, connections to this server are not required to be signed. Windows 11 refuses guest access by default, and guest connections are neither signed nor encrypted.',
	'shares.form.loadingUsers': 'Loading users...',
	'shares.form.usersError': 'The users could not be loaded',
	'shares.form.notServedHint': 'Fix the folder first: a share that is not served cannot be given access',
	// Only removed or lowered access disconnects (ADR-0103).
	'shares.form.accessChangeNote':
		'Removing or lowering access, or turning guest access off, disconnects open connections to that share (clients reconnect); adding access disconnects nobody.',
	'shares.form.notSaved': 'Share was not saved',
	'shares.form.notCreated': 'Share was not created',
	'shares.form.save': 'Save',
	'shares.form.create': 'Create share',
	'shares.form.reloaded': 'The form now shows the current settings of the share: make your changes again.',
	'shares.form.reloadFailed': 'The share could not be loaded again: {message}',

	// The access picker of the share form: src/lib/components/shares/ShareAccessPicker.svelte.
	'shares.access.title': 'Access',
	'shares.access.noUsers': 'No users can be given access yet: add users on the Users page.',
	'shares.access.everyoneRead': 'Everyone: Read only',
	'shares.access.everyoneWrite': 'Everyone: Read & write',
	'shares.access.clear': 'Clear',
	'shares.access.adminsNotListed': 'Administrators are not listed: they cannot use SMB.',
	// Why a user's access does not count yet, by the SMB state of the user.
	'shares.access.passwordNeeded':
		'SMB is allowed, but the user has not turned it on yet: the access counts once SMB is on',
	'shares.access.off':
		'SMB is off for this user: the access counts once SMB is allowed on the Users page and turned on',

	// The SMB state of a user (SmbAccountState of supervisor/src/shares/account_state.py).
	'shares.smbState.active': 'Active',
	'shares.smbState.passwordNeeded': 'Password needed',
	'shares.smbState.off': 'Off',
	'shares.smbState.unavailable': 'Not available',

	// A share's card: src/lib/components/shares/ShareCard.svelte.
	'shares.card.notServed': 'Not served',
	'shares.card.guest': 'Guest: read only',
	'shares.card.hidden': 'Hidden',
	'shares.card.noAccess': 'No access',
	'shares.card.poolNotMounted': 'Pool not mounted',
	'shares.card.permissions': 'Permissions need repair',
	// The buttons, and what screen readers hear after their words: "Edit share docs".
	'shares.card.edit': 'Edit',
	'shares.card.editOf': 'share {name}',
	'shares.card.repair': 'Repair permissions',
	'shares.card.repairOf': 'of {name}',
	'shares.card.delete': 'Delete share {name}',
	'shares.card.dismiss': 'Dismiss',
	'shares.card.dismissOf': 'the notes of {name}',
	'shares.card.nobody': 'No user has access.',
	'shares.card.nobodyOwn': 'No user has access of their own.',
	// The groups of the access list: "Read & write: alice, bob · Read: carol".
	'shares.card.read': 'Read',
	// After an entry that does not count yet, for screen readers.
	'shares.card.notActive': '(not active yet: {reason})',
	'shares.card.crossedOut':
		'Crossed-out users cannot connect yet: their SMB access is not active (see the Users page).',
	'shares.card.crossedOutNotServed':
		'Crossed-out users cannot connect yet: their SMB access is not active (see the Users page), or the share is not served.',
	// Before the share's Windows and smb:// addresses.
	'shares.card.connect': 'Connect:',
	'shares.card.importNotes': 'Notes from importing the previous smb.conf',
	// Why an access entry does not count yet (it is not in smb.conf).
	'shares.card.reason.notServed': 'The share is not served',
	'shares.card.reason.passwordNeeded': 'SMB is allowed for {username}, but not turned on yet',
	'shares.card.reason.off': 'SMB is off for {username}: allow it on the Users page',
	'shares.card.reason.unavailable': '{username} cannot use SMB',
	'shares.card.reason.other': '{username} cannot connect yet',

	// Repair permissions: src/lib/components/shares/RepairPermissionsModal.svelte (ADR-0155).
	'shares.repair.title': 'Repair permissions of {name}',
	'shares.repair.lastError': 'The last result could not be read',
	'shares.repair.failed': 'Permissions were not repaired',
	'shares.repair.running': 'Repairing... This can take a few minutes.',
	'shares.repair.result': 'Repair result',
	'shares.repair.lastRun': 'Last run',
	// The states of a run (RepairResult of supervisor/src/shares/repair.py).
	'shares.repair.state.done': 'Done',
	'shares.repair.state.incomplete': 'Incomplete',
	'shares.repair.state.failed': 'Failed',
	// n: the errors.
	'shares.repair.counts': {
		one: '{visited} checked, {changed} changed, {n} error',
		other: '{visited} checked, {changed} changed, {n} errors'
	},
	'shares.repair.skipped': 'Skipped: {list}',
	// What a run skipped, by the keys of RepairResult.skipped.
	'shares.repair.skip.otherFilesystem': 'other volumes and mounts',
	'shares.repair.skip.snapshots': 'snapshots',
	'shares.repair.skip.symlink': 'links',
	'shares.repair.skip.special': 'special files',
	'shares.repair.skip.hardlink': 'hard-linked files',
	'shares.repair.skip.private': 'private files and folders',
	'shares.repair.skip.tooDeep': 'too deep',
	'shares.repair.skip.raced': 'changed while running',
	'shares.repair.skippedPaths': 'Skipped volumes and mount points:',
	'shares.repair.errors': 'Errors:',
	'shares.repair.intro':
		'Repair sets up the existing files and folders of this share for SMB users, as UNOS does for new share folders:',
	'shares.repair.group':
		'They get the group unos-smb, which can read and write them. Folders also keep new files in the group.',
	'shares.repair.others':
		'Other accounts lose access, including apps that run as their own user (for example a download or media app). Repair does not change ACL entries (set with setfacl): users and groups named there keep their access and can get more, and on such files the group unos-smb gets only what the ACL gives the owning group.',
	'shares.repair.owners': 'Owners and file contents do not change.',
	'shares.repair.private': 'Private files and folders (only their owner can use them) are left alone.',
	'shares.repair.skips': 'Snapshots, other volumes, mount points and links are skipped.',
	'shares.repair.duration':
		'It can take minutes on a large share, and stops after 4 minutes: run it again to continue.',
	'shares.repair.warning': 'This changes the permissions of existing files. It cannot be undone automatically.',
	'shares.repair.close': 'Close',
	'shares.repair.run': 'Repair permissions'
};

export const ru: Translation<typeof en> = {
	'shares.title': 'Общие папки',
	'shares.tabs.label': 'Виды общего доступа',
	'shares.pathsError': 'Не удалось прочитать пулы и тома',
	'shares.noPools':
		'Нет доступных пулов хранения. Сначала создайте пул хранения, затем добавляйте общие папки.',

	'shares.level.none': 'Нет доступа',
	'shares.level.read': 'Только чтение',
	'shares.level.write': 'Чтение и запись',

	'shares.location.label': 'Расположение',
	'shares.location.choose': 'Выберите пул или том',
	'shares.location.pool': '{name} (пул)',
	'shares.location.volume': '{name} (том)',
	'shares.location.noneMounted':
		'Не смонтирован ни один пул или том. Сначала смонтируйте пул на странице «Хранилище».',
	'shares.location.noneManaged': 'Не смонтирован ни один пул, которым управляет UNOS.',
	'shares.location.folder': 'Папка',

	'shares.smb.add': 'Добавить общую папку SMB',
	'shares.smb.title': 'Общие папки SMB',
	'shares.smb.loadError': 'Не удалось загрузить общие папки',
	'shares.smb.empty': 'Общих папок SMB пока нет',
	'shares.smb.emptyHint': 'Откройте выбранным пользователям доступ к папке пула или к тому.',
	'shares.smb.networkNote':
		'SMB отвечает только адресам вашей локальной сети (частные IPv4, IPv6 ULA и link-local, диапазоны VPN, например 100.64.0.0/10); публичные адреса отклоняются.',
	'shares.smb.created': 'Общая папка «{name}» создана',
	'shares.smb.saved': 'Общая папка «{name}» сохранена',
	'shares.smb.deleted': 'Общая папка «{name}» удалена',
	'shares.smb.deleteTitle': 'Удаление общей папки',
	'shares.smb.deleteBefore': 'Удалить общую папку',
	'shares.smb.deleteAfter': '? Папка и её файлы сохранятся. Открытые подключения к общей папке будут закрыты.',
	'shares.smb.notDeleted': 'Общая папка не удалена',
	'shares.smb.delete': 'Удалить общую папку',

	'shares.status.error': 'Не удалось прочитать состояние SMB',
	'shares.status.sambaMissing': 'Samba не установлена на хосте: общие папки и учётные записи SMB не работают',
	'shares.status.missing': 'Не найдены: {tools}',
	'shares.status.guestShares':
		'Гостевой доступ включён для: {shares}. Любой в вашей сети может читать эти общие папки без пароля, и подпись подключений к этому серверу не требуется.',
	'shares.status.imported': 'Общие папки, импортированные из прежнего smb.conf',
	'shares.status.unmanagedUsers':
		'Эти учётные записи Samba созданы не в UNOS и не могут пользоваться общими папками UNOS: {users}',
	'shares.status.nobody':
		'Пока никто не может подключиться: разрешите SMB на странице «Пользователи», затем выдайте доступ здесь.',

	'shares.nfs.add': 'Добавить экспорт NFS',
	'shares.nfs.title': 'Экспорты NFS',
	'shares.nfs.serverMissing': 'NFS-сервер не установлен на хосте: ни один компьютер не может смонтировать экспорты',
	'shares.nfs.installServer': 'Установите на NAS пакет nfs-kernel-server.',
	'shares.nfs.statusError': 'Не удалось прочитать состояние NFS',
	'shares.nfs.loadError': 'Не удалось загрузить экспорты NFS',
	'shares.nfs.empty': 'Экспортов NFS пока нет',
	'shares.nfs.emptyHint': 'Экспортируйте папку пула или том для компьютеров сети.',
	'shares.nfs.clientsOf': 'Клиенты {path}',
	'shares.nfs.perFlavor': 'Зависит от метода защиты',
	'shares.nfs.notAddedInUnos': 'В {file}, строка {line}: добавлен не в UNOS, поэтому изменяйте его на NAS',
	'shares.nfs.deleteOne': 'Удалить экспорт {path}',
	'shares.nfs.note':
		'NFS не спрашивает пароль: любой компьютер сети клиентов может смонтировать экспорт, а его пользователи получают в папке тот доступ, который дают идентификаторы пользователя и группы их собственных учётных записей (root на клиенте считается nobody). UNOS экспортирует папки только для адресов вашей локальной сети.',
	'shares.nfs.created': 'Экспорт NFS папки {path} создан',
	'shares.nfs.deleted': 'Экспорт NFS папки {path} удалён',
	'shares.nfs.deleteTitle': 'Удаление экспорта NFS',
	'shares.nfs.deleteBefore': 'Удалить экспорт папки',
	'shares.nfs.deleteAfter': '? Клиенты NFS потеряют доступ к ней. Папка и её файлы сохранятся.',
	'shares.nfs.notDeleted': 'Экспорт не удалён',
	'shares.nfs.delete': 'Удалить экспорт',

	'shares.nfs.form.title': 'Новый экспорт NFS',
	'shares.nfs.form.folderPlaceholder': 'media',
	'shares.nfs.form.volumeHint':
		'Существующая папка внутри тома. Оставьте поле пустым, чтобы экспортировать весь том',
	'shares.nfs.form.poolHint': 'Существующая папка внутри пула, например созданная в «Файлах»',
	'shares.nfs.form.exportedFolder': 'Экспортируемая папка:',
	'shares.nfs.form.client': 'Сеть клиентов',
	'shares.nfs.form.clientHint':
		'Один адрес или сеть вашей локальной сети, например 192.168.1.0/24, 192.168.1.20 или fd00::/64',
	'shares.nfs.form.clientRequired': 'Введите адрес или сеть',
	'shares.nfs.form.notCreated': 'Экспорт не создан',
	'shares.nfs.form.create': 'Создать экспорт',
	'shares.nfs.form.unmanagedPools':
		'Пулы, которые на странице «Хранилище» отмечены как не монтируемые при загрузке, не показаны: экспортам NFS нужен пул, которым управляет UNOS.',
	'shares.nfs.form.poolRoot':
		'Экспортируйте папку внутри пула или том: верхний уровень пула содержит данные приложений (apps/), тома и снимки',
	'shares.nfs.form.appsFolder':
		'Данные приложений ({pool}/apps) нельзя экспортировать: там приложения хранят свои настройки',

	'shares.form.addTitle': 'Новая общая папка SMB',
	'shares.form.editTitle': 'Изменение общей папки {name}',
	'shares.form.name': 'Имя',
	'shares.form.namePlaceholder': 'documents',
	'shares.form.nameRequired': 'Введите имя общей папки',
	'shares.form.notServed': 'Эта общая папка не раздаётся',
	'shares.form.cannotBeShared': 'Её папку нельзя сделать общей.',
	'shares.form.currentFolderBefore': 'Папка сейчас:',
	'shares.form.currentFolderAfter': '. Выберите папку, которую можно сделать общей.',
	'shares.form.notServedGrants':
		'Пока общая папка не раздаётся, её записи доступа и гостевой доступ не действуют. Чтобы сохранить другие изменения без новой папки, уберите их: «Нет доступа» для каждого пользователя и выключенный гостевой доступ.',
	'shares.form.unmanagedPools':
		'Пулы, которые на странице «Хранилище» отмечены как не монтируемые при загрузке, не показаны: общим папкам SMB нужен пул, которым управляет UNOS.',
	'shares.form.folderPlaceholder': 'documents',
	'shares.form.volumeHint':
		'Папка внутри пула или тома; если её нет, она будет создана. Оставьте поле пустым, чтобы открыть доступ ко всему тому',
	'shares.form.poolHint': 'Папка внутри пула или тома; если её нет, она будет создана.',
	'shares.form.sharedFolder': 'Общая папка:',
	'shares.form.poolRoot':
		'Откройте доступ к папке внутри пула или к тому: верхний уровень пула содержит данные приложений (apps/), тома и снимки',
	'shares.form.appsFolder':
		'Данные приложений ({pool}/apps) нельзя сделать общими: там приложения хранят свои настройки',
	'shares.form.appsNote':
		'Папки, которые используют приложения (загрузки, медиа), можно сделать общими. Тогда пользователи SMB с правом записи меняют то, что видит приложение.',
	'shares.form.comment': 'Комментарий',
	'shares.form.browseable': 'Показывать в сетевом окружении',
	'shares.form.guest': 'Гости могут читать эту общую папку без пароля',
	'shares.form.guestWarning':
		'Любой в вашей сети может читать эту общую папку без пароля, и её имя видно всем в вашей сети. Гости никогда не могут записывать. Пока существует гостевая общая папка, подпись подключений к этому серверу не требуется. Windows 11 по умолчанию отказывает в гостевом доступе, а гостевые подключения не подписываются и не шифруются.',
	'shares.form.loadingUsers': 'Загрузка пользователей...',
	'shares.form.usersError': 'Не удалось загрузить пользователей',
	'shares.form.notServedHint': 'Сначала исправьте папку: общей папке, которая не раздаётся, нельзя выдать доступ',
	'shares.form.accessChangeNote':
		'Если убрать или понизить доступ либо выключить гостевой доступ, открытые подключения к этой общей папке разрываются (клиенты подключатся снова); добавление доступа никого не отключает.',
	'shares.form.notSaved': 'Общая папка не сохранена',
	'shares.form.notCreated': 'Общая папка не создана',
	'shares.form.save': 'Сохранить',
	'shares.form.create': 'Создать общую папку',
	'shares.form.reloaded': 'Теперь форма показывает текущие настройки общей папки: внесите изменения ещё раз.',
	'shares.form.reloadFailed': 'Не удалось снова загрузить общую папку: {message}',

	'shares.access.title': 'Доступ',
	'shares.access.noUsers': 'Пока некому выдать доступ: добавьте пользователей на странице «Пользователи».',
	'shares.access.everyoneRead': 'Всем: только чтение',
	'shares.access.everyoneWrite': 'Всем: чтение и запись',
	'shares.access.clear': 'Сбросить',
	'shares.access.adminsNotListed': 'Администраторы не показаны: они не могут пользоваться SMB.',
	'shares.access.passwordNeeded':
		'SMB разрешён, но пользователь ещё не включил его: доступ начнёт действовать, когда SMB будет включён',
	'shares.access.off':
		'SMB для этого пользователя выключен: доступ начнёт действовать, когда SMB разрешат на странице «Пользователи» и включат',

	'shares.smbState.active': 'Включён',
	'shares.smbState.passwordNeeded': 'Нужен пароль',
	'shares.smbState.off': 'Выключен',
	'shares.smbState.unavailable': 'Недоступен',

	'shares.card.notServed': 'Не раздаётся',
	'shares.card.guest': 'Гости: только чтение',
	'shares.card.hidden': 'Скрыта',
	'shares.card.noAccess': 'Нет доступа',
	'shares.card.poolNotMounted': 'Пул не смонтирован',
	'shares.card.permissions': 'Нужно исправить права',
	'shares.card.edit': 'Изменить',
	'shares.card.editOf': 'общую папку {name}',
	'shares.card.repair': 'Исправить права',
	'shares.card.repairOf': 'общей папки {name}',
	'shares.card.delete': 'Удалить общую папку {name}',
	'shares.card.dismiss': 'Скрыть',
	'shares.card.dismissOf': 'заметки общей папки {name}',
	'shares.card.nobody': 'Ни у одного пользователя нет доступа.',
	'shares.card.nobodyOwn': 'Ни у одного пользователя нет собственного доступа.',
	'shares.card.read': 'Чтение',
	'shares.card.notActive': '(ещё не действует: {reason})',
	'shares.card.crossedOut':
		'Зачёркнутые пользователи пока не могут подключиться: их доступ по SMB не включён (см. страницу «Пользователи»).',
	'shares.card.crossedOutNotServed':
		'Зачёркнутые пользователи пока не могут подключиться: их доступ по SMB не включён (см. страницу «Пользователи») или общая папка не раздаётся.',
	'shares.card.connect': 'Подключение:',
	'shares.card.importNotes': 'Заметки об импорте прежнего smb.conf',
	'shares.card.reason.notServed': 'Общая папка не раздаётся',
	'shares.card.reason.passwordNeeded': 'SMB разрешён для {username}, но ещё не включён',
	'shares.card.reason.off': 'SMB для {username} выключен: разрешите его на странице «Пользователи»',
	'shares.card.reason.unavailable': '{username} не может пользоваться SMB',
	'shares.card.reason.other': '{username} пока не может подключиться',

	'shares.repair.title': 'Исправление прав общей папки {name}',
	'shares.repair.lastError': 'Не удалось прочитать последний результат',
	'shares.repair.failed': 'Права не исправлены',
	'shares.repair.running': 'Исправление... Это может занять несколько минут.',
	'shares.repair.result': 'Результат исправления',
	'shares.repair.lastRun': 'Последний запуск',
	'shares.repair.state.done': 'Готово',
	'shares.repair.state.incomplete': 'Не завершено',
	'shares.repair.state.failed': 'Ошибка',
	// Counted after a colon, the words do not change with the count.
	'shares.repair.counts': {
		one: 'Проверено: {visited}, изменено: {changed}, ошибок: {n}',
		few: 'Проверено: {visited}, изменено: {changed}, ошибок: {n}',
		many: 'Проверено: {visited}, изменено: {changed}, ошибок: {n}'
	},
	'shares.repair.skipped': 'Пропущено: {list}',
	'shares.repair.skip.otherFilesystem': 'другие тома и точки монтирования',
	'shares.repair.skip.snapshots': 'снимки',
	'shares.repair.skip.symlink': 'ссылки',
	'shares.repair.skip.special': 'специальные файлы',
	'shares.repair.skip.hardlink': 'файлы с жёсткими ссылками',
	'shares.repair.skip.private': 'личные файлы и папки',
	'shares.repair.skip.tooDeep': 'слишком глубокая вложенность',
	'shares.repair.skip.raced': 'изменились во время работы',
	'shares.repair.skippedPaths': 'Пропущенные тома и точки монтирования:',
	'shares.repair.errors': 'Ошибки:',
	'shares.repair.intro':
		'Исправление настраивает существующие файлы и папки этой общей папки для пользователей SMB так же, как UNOS настраивает новые общие папки:',
	'shares.repair.group':
		'Они получают группу unos-smb, которая может их читать и изменять. Новые файлы в папках тоже получают эту группу.',
	'shares.repair.others':
		'Другие учётные записи теряют доступ, в том числе приложения, которые работают от имени своего пользователя (например, приложение для загрузок или медиа). Исправление не меняет записи ACL (заданные через setfacl): названные в них пользователи и группы сохраняют свой доступ и могут получить больше, а группа unos-smb получает на таких файлах только то, что ACL даёт группе-владельцу.',
	'shares.repair.owners': 'Владельцы и содержимое файлов не меняются.',
	'shares.repair.private': 'Личные файлы и папки (доступные только владельцу) не затрагиваются.',
	'shares.repair.skips': 'Снимки, другие тома, точки монтирования и ссылки пропускаются.',
	'shares.repair.duration':
		'На большой общей папке это может занять несколько минут, а через 4 минуты исправление останавливается: запустите его снова, чтобы продолжить.',
	'shares.repair.warning': 'Это меняет права существующих файлов. Автоматически отменить это нельзя.',
	'shares.repair.close': 'Закрыть',
	'shares.repair.run': 'Исправить права'
};
