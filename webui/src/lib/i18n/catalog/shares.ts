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
	// Under the header's Add button while no pool can hold one.
	'shares.noPoolsShort': 'Create a storage pool first',
	// The empty tab's next step when the button above cannot be used yet.
	'shares.toStorage': 'Go to Storage →',

	// The access levels (ShareAccessLevel), and none.
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
	'shares.smb.emptyHint': 'Use Add SMB share above to share a folder of a pool, or a volume, with the users you choose.',
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

	// The network interfaces that SMB listens on (GET /shares/smb-interfaces, ADR-0391): the card of
	// the SMB tab, its dialog and Restart SMB. The server's problems are shown as they come.
	'shares.interfaces.title': 'Network interfaces',
	'shares.interfaces.loadError': 'The network interfaces of SMB could not be read',
	'shares.interfaces.every': 'SMB listens on every network interface of the NAS.',
	'shares.interfaces.only': 'SMB listens only on {names}, and on the NAS itself (the loopback).',
	'shares.interfaces.onlyLoopback': 'SMB listens only on the NAS itself (the loopback).',
	'shares.interfaces.listening': 'SMB answers on:',
	'shares.interfaces.listeningUnknown': 'unknown',
	'shares.interfaces.listeningNone': 'no address',
	'shares.interfaces.everyIpv4': 'every IPv4 address',
	'shares.interfaces.everyIpv6': 'every IPv6 address',
	'shares.interfaces.choose': 'Choose interfaces…',
	'shares.interfaces.restart': 'Restart SMB…',
	'shares.interfaces.savedEvery': 'SMB listens on every network interface again.',
	'shares.interfaces.savedSome': 'SMB listens only on {names} now.',
	'shares.interfaces.restarted': 'SMB was restarted.',
	'shares.interfaces.dialogTitle': 'Network interfaces of SMB',
	'shares.interfaces.listenOn': 'SMB listens on',
	'shares.interfaces.modeEvery': 'Every network interface (default)',
	'shares.interfaces.modeChosen': 'Only the interfaces chosen below',
	'shares.interfaces.list': 'Interfaces of the NAS',
	'shares.interfaces.none': 'No network interface of the NAS could be listed.',
	'shares.interfaces.noAddress': 'No address',
	'shares.interfaces.notOnHost': 'Not on the NAS now',
	'shares.interfaces.disabled': 'Disabled',
	'shares.interfaces.noLan': 'No address of the local network',
	'shares.interfaces.pointToPoint': 'Point-to-point: Samba does not listen here',
	'shares.interfaces.loopbackNote':
		"The NAS itself (the loopback) stays bound. Docker's interfaces are not listed: apps reach SMB at the NAS's address.",
	'shares.interfaces.addressNote':
		'smbd listens on the addresses that the chosen interfaces have when it starts. Give the NAS a fixed address in your router; when an address changes, restart SMB here.',
	'shares.interfaces.discoveryNote':
		'Computers on the networks of other interfaces may still list the NAS, but cannot connect.',
	'shares.interfaces.pickOne': 'Choose at least one interface, or every interface.',
	'shares.interfaces.more': 'How SMB listens',
	'shares.interfaces.noChanges': 'No changes to save',
	'shares.interfaces.restartWarning': {
		one: 'Saving restarts SMB: the {n} open SMB connection is closed, and its computer connects again.',
		other: 'Saving restarts SMB: the {n} open SMB connections are closed, and their computers connect again.'
	},
	'shares.interfaces.restartWarningNone': 'Saving restarts SMB; no SMB connection is open now.',
	'shares.interfaces.restartWarningAny':
		'Saving restarts SMB: every open SMB connection is closed, and the computers connect again.',
	'shares.interfaces.notSaved': 'The interfaces were not saved',
	'shares.interfaces.save': 'Save and restart SMB',
	'shares.interfaces.restartTitle': 'Restart SMB',
	'shares.interfaces.restartText':
		'smbd restarts and then listens on the addresses that the chosen interfaces have now.',
	'shares.interfaces.restartSessions': {
		one: 'The {n} open SMB connection is closed.',
		other: 'The {n} open SMB connections are closed.'
	},
	'shares.interfaces.restartSessionsNone': 'No SMB connection is open now.',
	'shares.interfaces.restartSessionsAny': 'Every open SMB connection is closed.',
	'shares.interfaces.notRestarted': 'SMB was not restarted',
	'shares.interfaces.restartNow': 'Restart SMB',

	// GET /shares/smb-status.
	'shares.status.error': 'The SMB status could not be read',
	'shares.status.sambaMissing': 'Samba is not installed on the host: shares and SMB accounts cannot be used',
	'shares.status.missing': 'Missing: {tools}',
	'shares.status.guestShares':
		'Guest access is on for: {shares}. Anyone on your network can read these shares without a password, and connections to this server are not required to be signed.',
	'shares.status.imported': { one: 'Shares imported from the previous smb.conf: {n} note', other: 'Shares imported from the previous smb.conf: {n} notes' },
	'shares.status.dismissImport': 'Dismiss the notes',
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
	'shares.nfs.emptyHint': 'Use Add NFS export above to export a folder of a pool, or a volume, to the computers of a network.',
	'shares.nfs.clientsOf': 'Clients of {path}',
	// A client whose options differ by security flavor (sec=): neither read only nor read & write.
	'shares.nfs.perFlavor': 'Per security flavor',
	// An export that another program or the owner wrote; and the raw options of exportfs, under Details.
	'shares.nfs.notUnos': 'Not from UNOS',
	'shares.nfs.options': 'Options',
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
		'Its access entries and guest access do not count while it is not served. To save other changes without a new folder, remove them: No access for every user and group, and guest access off.',
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
	'shares.form.guestShort': 'Anyone on your network can read this share without a password.',
	'shares.form.details': 'Details',
	// As the server words its warning about a guest share (ADR-0096).
	'shares.form.guestWarning':
		'Anyone on your network can read this share without a password, and its name is visible to anyone on your network. Guests can never write. While a guest share exists, connections to this server are not required to be signed. Windows 11 refuses guest access by default, and guest connections are neither signed nor encrypted.',
	// The protocol options of a share (ADR-0380).
	'shares.form.options': 'Protocol options',
	'shares.form.option.encryption': 'Require encryption',
	'shares.form.option.encryptionHint': 'Only clients that encrypt (SMB 3) can connect; guests cannot.',
	'shares.form.option.encryptionMore':
		"Only clients that encrypt the connection (SMB 3) can open the share; older ones, such as SMB 2 clients, are refused. Guests' connections are never encrypted, so a guest share cannot require it.",
	'shares.form.option.timeMachine': 'Time Machine backups of Macs',
	'shares.form.option.timeMachineHint': 'Macs back up here; whoever backs up needs read & write.',
	'shares.form.option.timeMachineMore':
		"Macs find the share in the Time Machine settings and back up to it; whoever backs up needs read & write access. Time Machine takes the free space of the pool. While a share offers Time Machine, every share loads Apple's SMB extensions.",
	'shares.form.option.previousVersions': 'Previous Versions from snapshots',
	'shares.form.option.previousVersionsHint': 'Windows lists the snapshots of the share\'s pool or volume.',
	'shares.form.option.previousVersionsMore':
		"Windows lists the snapshots of the share's pool, or of its volume when the share is in one, under Previous Versions of its files and folders: those made on the Snapshots page without a name of your own, and for a pool those of scheduled tasks too, which snapshot pools only. Open connections get it when they reconnect; turning it off disconnects them.",
	'shares.form.usersError': 'The users could not be loaded',
	'shares.form.notServedHint': 'Fix the folder first: a share that is not served cannot be given access',
	'shares.form.notSaved': 'Share was not saved',
	'shares.form.notCreated': 'Share was not created',
	'shares.form.save': 'Save',
	'shares.form.create': 'Create share',
	'shares.form.reloaded': 'The form now shows the current settings of the share: make your changes again.',
	'shares.form.reloadFailed': 'The share could not be loaded again: {message}',
	// The question before a save that lowers access: what is lowered, one line each.
	'shares.form.lower.title': 'Close open connections?',
	'shares.form.lower.intro': 'Saving closes the open connections to {name}:',
	'shares.form.lower.list': 'What the save takes away',
	'shares.form.lower.user': '{name}: {from} → {to}',
	'shares.form.lower.group': 'Group {name}: {from} → {to}',
	'shares.form.lower.guest': 'Guest access turned off',
	'shares.form.lower.encryption': 'Encryption required',
	'shares.form.lower.previousVersions': 'Previous Versions turned off',
	'shares.form.lower.reconnect': 'Computers connect again on their own, with the new access.',
	'shares.form.lower.confirm': 'Save and disconnect',

	// The access picker of the share form: src/lib/components/shares/ShareAccessPicker.svelte.
	'shares.access.title': 'Access',
	// The access table: the columns' heads, its two parts, the All users row, the levels.
	'shares.access.who': 'Who',
	'shares.access.level': 'Access',
	'shares.access.users': 'Users',
	'shares.access.allUsers': 'All users',
	'shares.access.allUsersHint': 'Sets the level of every user below',
	'shares.access.none': 'None',
	'shares.access.read': 'Read',
	'shares.access.write': 'Read & write',
	'shares.access.addUser': 'Add a user →',
	'shares.access.createGroup': 'Create a group →',
	// The links of the table open Users in a new tab, so that the share's form stays as it is.
	'shares.access.newTab': '(opens in a new tab)',
	// Under a user's name: the kind, or why the access does not count yet.
	'shares.access.kindUser': 'User',
	'shares.access.smbOff': 'SMB off',
	'shares.access.turnOnSmb': 'Turn SMB on in Users →',
	'shares.access.smbPending': 'SMB not on yet',
	'shares.access.smbPendingHint': 'counts once they turn it on',
	'shares.access.smbUnavailable': 'No SMB',
	'shares.access.groupNoMembers': 'Group · no members yet',
	'shares.access.noUsers': 'No users yet.',
	'shares.access.adminsNotListed': 'Administrators are not listed: they cannot use SMB.',
	// The groups of the picker: "Group family", and its members under the level.
	'shares.access.groups': 'Groups',
	'shares.access.noGroups': 'No groups yet.',
	'shares.access.groupLabel': 'Group {name}',
	'shares.access.groupMembers': { one: 'Group · {n} member: {names}', other: 'Group · {n} members: {names}' },
	'shares.access.groupsHint': 'A group\'s members get its level once their SMB is on; whoever has several levels gets the highest.',

	// The SMB state of a user (SmbAccountState of supervisor/src/shares/account_state.py).
	'shares.smbState.active': 'SMB on',
	'shares.smbState.passwordNeeded': 'SMB: password needed',
	'shares.smbState.off': 'SMB off',
	'shares.smbState.unavailable': 'No SMB',

	// A share's card: src/lib/components/shares/ShareCard.svelte.
	'shares.card.notServed': 'Not served',
	'shares.card.guest': 'Guest: read only',
	'shares.card.hidden': 'Hidden',
	'shares.card.encryption': 'Encryption required',
	'shares.card.timeMachine': 'Time Machine',
	'shares.card.previousVersions': 'Previous Versions',
	// A stored setting that smb.conf leaves out, with the server's reason (ADR-0380).
	'shares.card.off.guest': 'Guest access does not work here',
	'shares.card.off.timeMachine': 'Time Machine does not work here',
	'shares.card.off.previousVersions': 'Previous Versions shows nothing here',
	'shares.card.noAccess': 'Nobody can connect yet',
	'shares.card.poolNotMounted': 'Pool not mounted',
	'shares.card.permissions': 'Permissions need repair',
	'shares.card.repairing': 'Repairing permissions',
	// The buttons, and what screen readers hear after their words: "Edit share docs".
	'shares.card.edit': 'Edit',
	'shares.card.editOf': 'share {name}',
	'shares.card.repair': 'Repair permissions',
	'shares.card.repairOf': 'of {name}',
	'shares.card.delete': 'Delete share {name}',
	'shares.card.deleteShort': 'Delete',
	// The copy buttons of the addresses.
	'shares.card.copyWindows': 'Copy the Windows address',
	'shares.card.copyUrl': 'Copy the smb:// address',
	'shares.card.copied': 'Copied',
	'shares.card.copyFailed': 'Could not copy: select the address and copy it yourself.',
	'shares.card.dismiss': 'Dismiss',
	'shares.card.dismissOf': 'the notes of {name}',
	'shares.card.nobody': 'No user has access.',
	'shares.card.nobodyOwn': 'No user has access of their own.',
	// The groups of the access list: "Read & write: alice, bob · Read: carol".
	'shares.card.read': 'Read',
	// A group's access: "Group family (Read & write): bob, carol".
	'shares.card.group': 'Group {group} ({level}):',
	'shares.card.groupEmpty': 'no members',
	// A user whose own level is Read but who writes through a group.
	'shares.card.raised': '{username} also writes through a group: the higher level counts.',
	// After an entry that does not count yet, for screen readers.
	'shares.card.notActive': '(not active yet: {reason})',
	// Before the share's Windows and smb:// addresses.
	'shares.card.connect': 'Connect:',
	'shares.card.importNotes': 'Notes from importing the previous smb.conf',
	// Why an access entry does not count yet (it is not in smb.conf).
	'shares.card.reason.notServed': 'The share is not served',
	'shares.card.reason.passwordNeeded': 'SMB is allowed for {username}, but not turned on yet',
	'shares.card.reason.off': 'SMB is off for {username}: allow it on the Users page',
	'shares.card.reason.unavailable': '{username} cannot use SMB',
	'shares.card.reason.other': '{username} cannot connect yet',
	// The short tag after a name whose access does not count yet.
	'shares.card.tag.passwordNeeded': 'SMB not on yet',
	'shares.card.tag.off': 'SMB off',
	'shares.card.tag.unavailable': 'No SMB',
	'shares.card.tag.other': 'not active',

	// Repair permissions: src/lib/components/shares/RepairPermissionsModal.svelte (ADR-0363).
	'shares.repair.title': 'Repair permissions of {name}',
	'shares.repair.lastError': 'The last result could not be read',
	'shares.repair.failed': 'Permissions were not repaired',
	'shares.repair.notStopped': 'The repair was not stopped',
	// A repair that runs in the background, and when it started.
	'shares.repair.progress': 'Repair in progress',
	'shares.repair.since': 'started {time}',
	'shares.repair.running':
		'It runs in the background: you can close this dialog, and the Shares page shows when it ends.',
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
		'It runs in the background until the whole share is done, which can take hours on a large share. Stop repair ends it early; a new run continues quickly.',
	'shares.repair.warning': 'This changes the permissions of existing files. It cannot be undone automatically.',
	'shares.repair.close': 'Close',
	'shares.repair.run': 'Repair permissions',
	'shares.repair.stop': 'Stop repair',

	// Several shares at once (ADR-0389): the selection above the list, its one question and the
	// results. A share's status in the answer is shown through shares.bulk.status.*.
	'shares.bulk.toolbar': 'Selected shares',
	'shares.bulk.selectAll': 'Select all',
	'shares.bulk.selected': { one: '{n} share selected', other: '{n} shares selected' },
	'shares.bulk.delete': 'Delete selected',
	'shares.card.select': 'Select share {name}',
	'shares.bulk.deleteTitle': { one: 'Delete {n} share', other: 'Delete {n} shares' },
	'shares.bulk.question': {
		one: 'Delete this share? Open connections to it are closed.',
		other: 'Delete these {n} shares? Open connections to them are closed.'
	},
	'shares.bulk.list': 'Shares to delete',
	'shares.bulk.kept': {
		one: 'The folder and the files in it are kept.',
		other: 'Their folders and the files in them are kept.'
	},
	'shares.bulk.confirm': { one: 'Delete {n} share', other: 'Delete {n} shares' },
	'shares.bulk.noneDeleted': 'No share was deleted',
	'shares.bulk.someGone': 'The deletion failed, and some of these shares are no longer listed',
	'shares.bulk.notKnown': 'The deletion failed, and the list could not be read again: which shares are left is not known',
	'shares.bulk.tooMany': 'One deletion takes at most {max} shares: select fewer',
	'shares.bulk.deleted': { one: 'Deleted {n} share: {names}', other: 'Deleted {n} shares: {names}' },
	'shares.bulk.notDeleted': { one: '{n} share was not deleted', other: '{n} shares were not deleted' },
	'shares.bulk.notDeletedItem': '{name}: {reason}',
	'shares.bulk.status.notFound': 'no share has this name any more: it was deleted or renamed meanwhile',
	'shares.bulk.status.changed': 'it was changed meanwhile, so it was not deleted: check it and delete it again'
};

export const ru: Translation<typeof en> = {
	'shares.title': 'Общие папки',
	'shares.tabs.label': 'Виды общего доступа',
	'shares.pathsError': 'Не удалось прочитать пулы и тома',
	'shares.noPoolsShort': 'Сначала создайте пул хранения',
	'shares.toStorage': 'Перейти в «Хранилище» →',

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
	'shares.smb.emptyHint': 'Кнопка «Добавить общую папку SMB» выше открывает выбранным пользователям доступ к папке пула или к тому.',
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

	'shares.interfaces.title': 'Сетевые интерфейсы',
	'shares.interfaces.loadError': 'Не удалось прочитать сетевые интерфейсы SMB',
	'shares.interfaces.every': 'SMB принимает подключения на всех сетевых интерфейсах NAS.',
	'shares.interfaces.only': 'SMB принимает подключения только на {names} и на самом NAS (loopback).',
	'shares.interfaces.onlyLoopback': 'SMB принимает подключения только на самом NAS (loopback).',
	'shares.interfaces.listening': 'SMB отвечает на:',
	'shares.interfaces.listeningUnknown': 'неизвестно',
	'shares.interfaces.listeningNone': 'ни одном адресе',
	'shares.interfaces.everyIpv4': 'всех адресах IPv4',
	'shares.interfaces.everyIpv6': 'всех адресах IPv6',
	'shares.interfaces.choose': 'Выбрать интерфейсы…',
	'shares.interfaces.restart': 'Перезапустить SMB…',
	'shares.interfaces.savedEvery': 'SMB снова принимает подключения на всех сетевых интерфейсах.',
	'shares.interfaces.savedSome': 'Теперь SMB принимает подключения только на {names}.',
	'shares.interfaces.restarted': 'SMB перезапущен.',
	'shares.interfaces.dialogTitle': 'Сетевые интерфейсы SMB',
	'shares.interfaces.listenOn': 'SMB принимает подключения',
	'shares.interfaces.modeEvery': 'На всех сетевых интерфейсах (по умолчанию)',
	'shares.interfaces.modeChosen': 'Только на интерфейсах, выбранных ниже',
	'shares.interfaces.list': 'Интерфейсы NAS',
	'shares.interfaces.none': 'Не удалось получить список сетевых интерфейсов NAS.',
	'shares.interfaces.noAddress': 'Нет адреса',
	'shares.interfaces.notOnHost': 'Сейчас отсутствует на NAS',
	'shares.interfaces.disabled': 'Выключен',
	'shares.interfaces.noLan': 'Нет адреса локальной сети',
	'shares.interfaces.pointToPoint': 'Точка-точка: Samba здесь не принимает подключения',
	'shares.interfaces.loopbackNote':
		'Сам NAS (loopback) остаётся привязанным. Интерфейсы Docker не показаны: приложения подключаются к SMB по адресу NAS.',
	'shares.interfaces.addressNote':
		'smbd принимает подключения на адресах, которые есть у выбранных интерфейсов при его запуске. Закрепите за NAS постоянный адрес в роутере; если адрес изменится, перезапустите SMB здесь.',
	'shares.interfaces.discoveryNote':
		'Компьютеры в сетях других интерфейсов могут по-прежнему видеть NAS, но не смогут подключиться.',
	'shares.interfaces.pickOne': 'Выберите хотя бы один интерфейс или все интерфейсы.',
	'shares.interfaces.more': 'Как SMB принимает подключения',
	'shares.interfaces.noChanges': 'Нет изменений для сохранения',
	'shares.interfaces.restartWarning': {
		one: 'Сохранение перезапустит SMB: {n} открытое подключение SMB будет закрыто, и компьютер подключится заново.',
		few: 'Сохранение перезапустит SMB: {n} открытых подключения SMB будут закрыты, и компьютеры подключатся заново.',
		many: 'Сохранение перезапустит SMB: {n} открытых подключений SMB будут закрыты, и компьютеры подключатся заново.'
	},
	'shares.interfaces.restartWarningNone': 'Сохранение перезапустит SMB; открытых подключений SMB сейчас нет.',
	'shares.interfaces.restartWarningAny':
		'Сохранение перезапустит SMB: все открытые подключения SMB будут закрыты, и компьютеры подключатся заново.',
	'shares.interfaces.notSaved': 'Интерфейсы не сохранены',
	'shares.interfaces.save': 'Сохранить и перезапустить SMB',
	'shares.interfaces.restartTitle': 'Перезапуск SMB',
	'shares.interfaces.restartText':
		'smbd перезапустится и будет принимать подключения на адресах, которые есть у выбранных интерфейсов сейчас.',
	'shares.interfaces.restartSessions': {
		one: '{n} открытое подключение SMB будет закрыто.',
		few: '{n} открытых подключения SMB будут закрыты.',
		many: '{n} открытых подключений SMB будут закрыты.'
	},
	'shares.interfaces.restartSessionsNone': 'Открытых подключений SMB сейчас нет.',
	'shares.interfaces.restartSessionsAny': 'Все открытые подключения SMB будут закрыты.',
	'shares.interfaces.notRestarted': 'SMB не перезапущен',
	'shares.interfaces.restartNow': 'Перезапустить SMB',

	'shares.status.error': 'Не удалось прочитать состояние SMB',
	'shares.status.sambaMissing': 'Samba не установлена на хосте: общие папки и учётные записи SMB не работают',
	'shares.status.missing': 'Не найдены: {tools}',
	'shares.status.guestShares':
		'Гостевой доступ включён для: {shares}. Любой в вашей сети может читать эти общие папки без пароля, и подпись подключений к этому серверу не требуется.',
	'shares.status.imported': { one: 'Общие папки, импортированные из прежнего smb.conf: {n} заметка', few: 'Общие папки, импортированные из прежнего smb.conf: {n} заметки', many: 'Общие папки, импортированные из прежнего smb.conf: {n} заметок' },
	'shares.status.dismissImport': 'Скрыть заметки',
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
	'shares.nfs.emptyHint': 'Кнопка «Добавить экспорт NFS» выше экспортирует папку пула или том для компьютеров сети.',
	'shares.nfs.clientsOf': 'Клиенты {path}',
	'shares.nfs.perFlavor': 'Зависит от метода защиты',
	'shares.nfs.notUnos': 'Не из UNOS',
	'shares.nfs.options': 'Параметры',
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
		'Пока общая папка не раздаётся, её записи доступа и гостевой доступ не действуют. Чтобы сохранить другие изменения без новой папки, уберите их: «Нет доступа» для каждого пользователя и группы и выключенный гостевой доступ.',
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
	'shares.form.guestShort': 'Любой в вашей сети может читать эту общую папку без пароля.',
	'shares.form.details': 'Подробнее',
	'shares.form.guestWarning':
		'Любой в вашей сети может читать эту общую папку без пароля, и её имя видно всем в вашей сети. Гости никогда не могут записывать. Пока существует гостевая общая папка, подпись подключений к этому серверу не требуется. Windows 11 по умолчанию отказывает в гостевом доступе, а гостевые подключения не подписываются и не шифруются.',
	'shares.form.options': 'Параметры протокола',
	'shares.form.option.encryption': 'Требовать шифрование',
	'shares.form.option.encryptionHint': 'Подключиться могут только клиенты с шифрованием (SMB 3); гости не могут.',
	'shares.form.option.encryptionMore':
		'Открыть общую папку могут только клиенты, которые шифруют подключение (SMB 3); более старым, например клиентам SMB 2, отказывается. Гостевые подключения никогда не шифруются, поэтому гостевая общая папка не может требовать шифрования.',
	'shares.form.option.timeMachine': 'Резервные копии Time Machine с Mac',
	'shares.form.option.timeMachineHint': 'Mac сохраняют сюда резервные копии; тому, кто их делает, нужны чтение и запись.',
	'shares.form.option.timeMachineMore':
		'Mac находит общую папку в настройках Time Machine и сохраняет в неё резервные копии; тому, кто их делает, нужен доступ на чтение и запись. Time Machine занимает свободное место пула. Пока хотя бы одна общая папка предлагает Time Machine, все общие папки загружают расширения SMB от Apple.',
	'shares.form.option.previousVersions': 'Предыдущие версии из снимков',
	'shares.form.option.previousVersionsHint': 'Windows показывает снимки пула или тома этой общей папки.',
	'shares.form.option.previousVersionsMore':
		'Windows показывает в «Предыдущих версиях» файлов и папок общей папки снимки её пула, а если она в томе, то снимки тома: сделанные на странице «Снимки» без заданного вами имени, а для пула ещё и задачами по расписанию, которые снимают только пулы. Открытые подключения получат их после переподключения; выключение разрывает их.',
	'shares.form.usersError': 'Не удалось загрузить пользователей',
	'shares.form.notServedHint': 'Сначала исправьте папку: общей папке, которая не раздаётся, нельзя выдать доступ',
	'shares.form.notSaved': 'Общая папка не сохранена',
	'shares.form.notCreated': 'Общая папка не создана',
	'shares.form.save': 'Сохранить',
	'shares.form.create': 'Создать общую папку',
	'shares.form.reloaded': 'Теперь форма показывает текущие настройки общей папки: внесите изменения ещё раз.',
	'shares.form.reloadFailed': 'Не удалось снова загрузить общую папку: {message}',
	'shares.form.lower.title': 'Разорвать открытые подключения?',
	'shares.form.lower.intro': 'При сохранении открытые подключения к {name} разрываются:',
	'shares.form.lower.list': 'Что сохранение отнимает',
	'shares.form.lower.user': '{name}: {from} → {to}',
	'shares.form.lower.group': 'Группа {name}: {from} → {to}',
	'shares.form.lower.guest': 'Гостевой доступ выключен',
	'shares.form.lower.encryption': 'Шифрование обязательно',
	'shares.form.lower.previousVersions': 'Предыдущие версии выключены',
	'shares.form.lower.reconnect': 'Компьютеры подключатся снова сами, уже с новым доступом.',
	'shares.form.lower.confirm': 'Сохранить и разорвать',

	'shares.access.title': 'Доступ',
	'shares.access.who': 'Кто',
	'shares.access.level': 'Доступ',
	'shares.access.users': 'Пользователи',
	'shares.access.allUsers': 'Все пользователи',
	'shares.access.allUsersHint': 'Задаёт уровень всем пользователям ниже',
	'shares.access.none': 'Нет',
	'shares.access.read': 'Чтение',
	'shares.access.write': 'Чтение и запись',
	'shares.access.addUser': 'Добавить пользователя →',
	'shares.access.createGroup': 'Создать группу →',
	'shares.access.newTab': '(откроется в новой вкладке)',
	'shares.access.kindUser': 'Пользователь',
	'shares.access.smbOff': 'SMB выключен',
	'shares.access.turnOnSmb': 'Включить SMB в «Пользователях» →',
	'shares.access.smbPending': 'SMB ещё не включён',
	'shares.access.smbPendingHint': 'доступ заработает, когда пользователь его включит',
	'shares.access.smbUnavailable': 'Без SMB',
	'shares.access.groupNoMembers': 'Группа · участников пока нет',
	'shares.access.noUsers': 'Пользователей пока нет.',
	'shares.access.adminsNotListed': 'Администраторы не показаны: они не могут пользоваться SMB.',
	'shares.access.groups': 'Группы',
	'shares.access.noGroups': 'Групп пока нет.',
	'shares.access.groupLabel': 'Группа {name}',
	'shares.access.groupMembers': { one: 'Группа · {n} участник: {names}', few: 'Группа · {n} участника: {names}', many: 'Группа · {n} участников: {names}' },
	'shares.access.groupsHint': 'Участники группы получают её уровень, как только у них включён SMB; у кого несколько уровней, действует самый высокий.',

	'shares.smbState.active': 'SMB включён',
	'shares.smbState.passwordNeeded': 'SMB: нужен пароль',
	'shares.smbState.off': 'SMB выключен',
	'shares.smbState.unavailable': 'Без SMB',

	'shares.card.notServed': 'Не раздаётся',
	'shares.card.guest': 'Гости: только чтение',
	'shares.card.hidden': 'Скрыта',
	'shares.card.encryption': 'Шифрование обязательно',
	'shares.card.timeMachine': 'Time Machine',
	'shares.card.previousVersions': 'Предыдущие версии',
	'shares.card.off.guest': 'Гостевой доступ здесь не работает',
	'shares.card.off.timeMachine': 'Time Machine здесь не работает',
	'shares.card.off.previousVersions': 'Предыдущие версии здесь ничего не покажут',
	'shares.card.noAccess': 'Пока никто не может подключиться',
	'shares.card.poolNotMounted': 'Пул не смонтирован',
	'shares.card.permissions': 'Нужно исправить права',
	'shares.card.repairing': 'Исправление прав',
	'shares.card.edit': 'Изменить',
	'shares.card.editOf': 'общую папку {name}',
	'shares.card.repair': 'Исправить права',
	'shares.card.repairOf': 'общей папки {name}',
	'shares.card.delete': 'Удалить общую папку {name}',
	'shares.card.deleteShort': 'Удалить',
	'shares.card.copyWindows': 'Скопировать адрес для Windows',
	'shares.card.copyUrl': 'Скопировать адрес smb://',
	'shares.card.copied': 'Скопировано',
	'shares.card.copyFailed': 'Не удалось скопировать: выделите адрес и скопируйте его сами.',
	'shares.card.dismiss': 'Скрыть',
	'shares.card.dismissOf': 'заметки общей папки {name}',
	'shares.card.nobody': 'Ни у одного пользователя нет доступа.',
	'shares.card.nobodyOwn': 'Ни у одного пользователя нет собственного доступа.',
	'shares.card.read': 'Чтение',
	'shares.card.group': 'Группа {group} ({level}):',
	'shares.card.groupEmpty': 'нет участников',
	'shares.card.raised': '{username} может и записывать через группу: действует более высокий уровень.',
	'shares.card.notActive': '(ещё не действует: {reason})',
	'shares.card.connect': 'Подключение:',
	'shares.card.importNotes': 'Заметки об импорте прежнего smb.conf',
	'shares.card.reason.notServed': 'Общая папка не раздаётся',
	'shares.card.reason.passwordNeeded': 'SMB разрешён для {username}, но ещё не включён',
	'shares.card.reason.off': 'SMB для {username} выключен: разрешите его на странице «Пользователи»',
	'shares.card.reason.unavailable': '{username} не может пользоваться SMB',
	'shares.card.reason.other': '{username} пока не может подключиться',
	'shares.card.tag.passwordNeeded': 'SMB ещё не включён',
	'shares.card.tag.off': 'SMB выключен',
	'shares.card.tag.unavailable': 'Без SMB',
	'shares.card.tag.other': 'не действует',

	'shares.repair.title': 'Исправление прав общей папки {name}',
	'shares.repair.lastError': 'Не удалось прочитать последний результат',
	'shares.repair.failed': 'Права не исправлены',
	'shares.repair.notStopped': 'Исправление не остановлено',
	'shares.repair.progress': 'Исправление идёт',
	'shares.repair.since': 'начато {time}',
	'shares.repair.running':
		'Оно идёт в фоне: этот диалог можно закрыть, а страница общих папок покажет, когда оно закончится.',
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
		'Исправление идёт в фоне, пока не будет обработана вся общая папка; на большой папке это может занять часы. «Остановить исправление» прерывает его, а новый запуск быстро продолжит работу.',
	'shares.repair.warning': 'Это меняет права существующих файлов. Автоматически отменить это нельзя.',
	'shares.repair.close': 'Закрыть',
	'shares.repair.run': 'Исправить права',
	'shares.repair.stop': 'Остановить исправление',

	'shares.bulk.toolbar': 'Выбранные общие папки',
	'shares.bulk.selectAll': 'Выбрать все',
	'shares.bulk.selected': {
		one: 'Выбрана {n} общая папка',
		few: 'Выбраны {n} общие папки',
		many: 'Выбрано {n} общих папок'
	},
	'shares.bulk.delete': 'Удалить выбранные',
	'shares.card.select': 'Выбрать общую папку {name}',
	'shares.bulk.deleteTitle': {
		one: 'Удаление {n} общей папки',
		few: 'Удаление {n} общих папок',
		many: 'Удаление {n} общих папок'
	},
	'shares.bulk.question': {
		one: 'Удалить {n} общую папку? Открытые подключения будут закрыты.',
		few: 'Удалить эти {n} общие папки? Открытые подключения к ним будут закрыты.',
		many: 'Удалить эти {n} общих папок? Открытые подключения к ним будут закрыты.'
	},
	'shares.bulk.list': 'Удаляемые общие папки',
	'shares.bulk.kept': {
		one: 'Папка каждой общей папки и файлы в ней сохранятся.',
		few: 'Папка каждой общей папки и файлы в ней сохранятся.',
		many: 'Папка каждой общей папки и файлы в ней сохранятся.'
	},
	'shares.bulk.confirm': {
		one: 'Удалить {n} общую папку',
		few: 'Удалить {n} общие папки',
		many: 'Удалить {n} общих папок'
	},
	'shares.bulk.noneDeleted': 'Ни одна общая папка не удалена',
	'shares.bulk.someGone': 'Удаление не удалось, и некоторых из этих общих папок больше нет в списке',
	'shares.bulk.notKnown': 'Удаление не удалось, и список не удалось прочитать снова: неизвестно, какие общие папки остались',
	'shares.bulk.tooMany': 'За один раз можно удалить не больше {max} общих папок: выберите меньше',
	'shares.bulk.deleted': {
		one: 'Удалена {n} общая папка: {names}',
		few: 'Удалены {n} общие папки: {names}',
		many: 'Удалено {n} общих папок: {names}'
	},
	'shares.bulk.notDeleted': {
		one: '{n} общая папка не удалена',
		few: '{n} общие папки не удалены',
		many: '{n} общих папок не удалено'
	},
	'shares.bulk.notDeletedItem': '{name}: {reason}',
	'shares.bulk.status.notFound': 'общей папки с таким именем больше нет: её удалили или переименовали',
	'shares.bulk.status.changed': 'её изменили, поэтому она не удалена: проверьте её и удалите снова'
};
