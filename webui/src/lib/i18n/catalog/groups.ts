import type { Translation } from '../translate';

// The groups of users on the Users page: src/lib/components/users/GroupsCard.svelte.
// The server's messages (a refusal, the warnings of a change) stay in English (ADR-0287). A sentence
// with a group's name in bold is translated in parts around the name (ADR-0296).

export const en = {
	'groups.title': 'Groups ({count})',
	'groups.add': 'Add group',
	'groups.deleteShort': 'Delete',
	'groups.about': 'Give a group access on the Shares page: its members whose SMB is on get it.',
	'groups.loadFailed': 'The groups could not be loaded',
	'groups.empty': 'No groups yet.',
	'groups.members': 'Members:',
	'groups.noMembers': 'No members yet.',
	'groups.shares': 'Access:',
	// A share and the group's level on it: "docs (Read & write)".
	'groups.shareLevel': '{share} ({level})',
	'groups.level.read': 'Read only',
	'groups.level.write': 'Read & write',
	'groups.noShares': 'No share access yet: give it on the Shares page.',
	// The buttons, and what screen readers hear after their words: "Edit group family".
	'groups.edit': 'Edit',
	'groups.editOf': 'group {name}',
	'groups.deleteGroup': 'Delete group {name}',
	'groups.created': 'Group "{name}" created',
	'groups.saved': 'Group "{name}" saved',
	'groups.deleted': 'Group "{name}" deleted',
	// A change refused after its dialog closed: the group was renamed or deleted meanwhile.
	'groups.actionFailed': 'The group was not changed',

	'groups.form.addTitle': 'Add group',
	'groups.form.editTitle': 'Edit group {name}',
	'groups.form.name': 'Name',
	'groups.form.nameHint': '1–32 lowercase letters, digits, _ or -, starting with a letter',
	'groups.form.nameInvalid': 'Use 1–32 lowercase letters, digits, _ or -, starting with a letter',
	'groups.form.nameRequired': 'Enter a name',
	'groups.form.members': 'Members',
	'groups.form.noUsers': 'No users can be members yet: add users above.',
	'groups.form.adminsNotListed': 'Administrators are not listed: they cannot use SMB.',
	'groups.form.accessNote':
		'Members get the access that the group has on the Shares page. Removing a member disconnects that member from those shares; adding one disconnects nobody.',
	'groups.form.notSaved': 'Group was not saved',
	'groups.form.notCreated': 'Group was not created',
	'groups.form.reloaded': 'The form now shows the current members of the group: make your changes again.',
	'groups.form.save': 'Save',
	'groups.form.create': 'Create group',

	'groups.delete.title': 'Delete group',
	'groups.delete.before': 'Delete group',
	'groups.delete.after': '? Its members lose the access that the group gives them; the users stay.',
	'groups.delete.submit': 'Delete group',
	'groups.delete.failed': 'Group was not deleted'
};

export const ru: Translation<typeof en> = {
	'groups.title': 'Группы ({count})',
	'groups.add': 'Добавить группу',
	'groups.deleteShort': 'Удалить',
	'groups.about': 'Доступ группе выдаётся на странице «Общие папки»: его получают её участники с включённым SMB.',
	'groups.loadFailed': 'Не удалось загрузить группы',
	'groups.empty': 'Групп пока нет.',
	'groups.members': 'Участники:',
	'groups.noMembers': 'Участников пока нет.',
	'groups.shares': 'Доступ:',
	'groups.shareLevel': '{share} ({level})',
	'groups.level.read': 'Только чтение',
	'groups.level.write': 'Чтение и запись',
	'groups.noShares': 'Доступа к общим папкам пока нет: выдайте его на странице «Общие папки».',
	'groups.edit': 'Изменить',
	'groups.editOf': 'группу {name}',
	'groups.deleteGroup': 'Удалить группу {name}',
	'groups.created': 'Группа «{name}» создана',
	'groups.saved': 'Группа «{name}» сохранена',
	'groups.deleted': 'Группа «{name}» удалена',
	'groups.actionFailed': 'Группа не изменена',

	'groups.form.addTitle': 'Добавление группы',
	'groups.form.editTitle': 'Изменение группы {name}',
	'groups.form.name': 'Имя',
	'groups.form.nameHint': '1–32 строчные латинские буквы, цифры, _ или -, первая — буква',
	'groups.form.nameInvalid': 'Используйте 1–32 строчные латинские буквы, цифры, _ или -, первая — буква',
	'groups.form.nameRequired': 'Введите имя',
	'groups.form.members': 'Участники',
	'groups.form.noUsers': 'Пока некого добавить в группу: добавьте пользователей выше.',
	'groups.form.adminsNotListed': 'Администраторы не показаны: они не могут пользоваться SMB.',
	'groups.form.accessNote':
		'Участники получают доступ, который у группы есть на странице «Общие папки». Если убрать участника, его подключения к этим общим папкам разрываются; добавление участника никого не отключает.',
	'groups.form.notSaved': 'Группа не сохранена',
	'groups.form.notCreated': 'Группа не создана',
	'groups.form.reloaded': 'Теперь форма показывает текущих участников группы: внесите изменения ещё раз.',
	'groups.form.save': 'Сохранить',
	'groups.form.create': 'Создать группу',

	'groups.delete.title': 'Удаление группы',
	'groups.delete.before': 'Удалить группу',
	'groups.delete.after': '? Её участники потеряют доступ, который даёт группа; сами пользователи останутся.',
	'groups.delete.submit': 'Удалить группу',
	'groups.delete.failed': 'Группа не удалена'
};
