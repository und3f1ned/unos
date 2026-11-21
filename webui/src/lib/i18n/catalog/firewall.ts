import type { Translation } from '../translate';

// The firewall card of System > Security (src/lib/components/system/security/FirewallCard.svelte): the
// host's ufw, turned on and changed with a revert that undoes the change unless it is kept
// (ADR-0416).

export const en = {
	'firewall.title': 'Firewall',
	'firewall.active': 'Active',
	'firewall.inactive': 'Inactive',
	'firewall.notInstalled': 'Not installed',
	'firewall.configure': 'Configure',
	'firewall.description':
		"The host's ufw firewall decides which computers reach the services of the NAS. Before it turns the firewall on, UNOS lets the local networks reach the web UI, SSH, SMB and iSCSI.",
	'firewall.missing':
		'ufw is not installed on the host. Install the package ufw on the host (sudo apt install ufw), then open this page again.',
	'firewall.loadFailed': 'The firewall could not be read',
	'firewall.status': 'Firewall Status',
	'firewall.defaults': 'Default: Incoming {incoming}, Outgoing {outgoing}',
	'firewall.enable': 'Enable',
	'firewall.disable': 'Disable',
	'firewall.reset': 'Reset',
	'firewall.rules': 'Firewall Rules',
	'firewall.addRule': 'Add Rule',
	'firewall.noRules': 'The host has no firewall rules',
	'firewall.port': 'Port: {port}',
	'firewall.any': 'Any',
	'firewall.from': 'From: {source}',
	'firewall.to': 'To: {destination}',
	'firewall.unos': 'UNOS',
	'firewall.ipv6': 'IPv6',
	'firewall.route': 'Routed',
	'firewall.unosHint':
		'UNOS keeps its rules, which let the local networks reach the web UI, SSH, SMB, iSCSI and network discovery: they have no Delete.',
	'firewall.docker':
		"Over IPv4, ufw does not filter the ports that Docker publishes, those of the web UI and of the apps on Docker's networks; over IPv6 it does, and so it does for apps on the host's network, such as Home Assistant: their ports need rules.",
	'firewall.deleteRule': 'Delete rule {n}',
	'firewall.ruleNumber': 'Rule {n}',
	'firewall.enabledNotice': 'Firewall enabled',
	'firewall.alreadyOn': 'The firewall is on already',
	'firewall.disabledNotice': 'Firewall disabled',
	'firewall.added': 'Rule added',
	'firewall.deleted': 'Rule deleted',
	'firewall.resetDone': "Firewall reset to its defaults: it is off, with UNOS's rules",
	'firewall.kept': 'The firewall change is kept',
	'firewall.toggleFailed': 'Failed to toggle firewall',
	'firewall.addFailed': 'Failed to add rule',
	'firewall.deleteFailed': 'Failed to delete rule',
	'firewall.resetFailed': 'Failed to reset firewall',
	'firewall.keepFailed': 'The change could not be kept',
	'firewall.confirmDelete': "Delete this rule from the host's firewall?",
	'firewall.deleteAction': 'Delete rule',
	'firewall.resetTitle': 'Reset the firewall?',
	'firewall.confirmReset': "It is turned off, every rule is removed, and UNOS's rules are added again.",
	'firewall.resetAction': 'Reset firewall',

	// Turning it on.
	'firewall.enableTitle': 'Turn the firewall on?',
	'firewall.enableRules':
		'First UNOS adds rules that let the local networks reach the web UI (ports 80 and 443), SSH, SMB (445), iSCSI (3260) and network discovery: {networks}.',
	'firewall.enableRevert': {
		one: 'Then the firewall goes on, and it is turned off again after {n} second unless you keep it. Before you keep it, check from another computer that SSH and your shares still answer.',
		other:
			'Then the firewall goes on, and it is turned off again after {n} seconds unless you keep it. Before you keep it, check from another computer that SSH and your shares still answer.'
	},
	'firewall.enableOthers':
		'Other services, such as NFS, need rules of their own, and so does a computer that reaches the NAS only through a public IPv6 address.',
	'firewall.turnOn': 'Turn on',
	'firewall.notLocal':
		'This computer is not in the local networks: once the firewall is on, SSH and SMB from it need a rule of their own. Add it first.',
	'firewall.working': 'Turning the firewall on: on a small board this takes up to a minute.',
	'firewall.reverting': 'The host is undoing the firewall change: the card looks again in a few seconds.',
	'firewall.revertFailed':
		"The host could not undo the last firewall change: check the rules below, or turn the firewall off. The host's journal says why (journalctl -u unos-firewall-revert).",
	'firewall.on': 'On: {interface}',

	// A change that waits to be kept.
	'firewall.waiting': {
		one: 'This firewall change is undone in {n} second unless you keep it. Check first that SSH and your shares still answer.',
		other:
			'This firewall change is undone in {n} seconds unless you keep it. Check first that SSH and your shares still answer.'
	},
	'firewall.keep': 'Keep the change',
	'firewall.undone':
		'The time to keep the firewall change has run out: unless it was kept elsewhere, the firewall is back as it was before it.',
	'firewall.notYet':
		'The firewall change should have been undone by now: open this page again to see the firewall as it is.',

	// The dialog of a new rule.
	'firewall.addTitle': 'Add Firewall Rule',
	'firewall.action': 'Action',
	'firewall.allow': 'Allow',
	'firewall.deny': 'Deny',
	'firewall.reject': 'Reject',
	'firewall.limit': 'Limit',
	'firewall.protocol': 'Protocol',
	'firewall.portLabel': 'Port',
	'firewall.portPlaceholder': 'e.g., 8080, 6000:6007 or 80,443',
	'firewall.portRequired': 'A port is required',
	'firewall.source': 'Source (optional)',
	'firewall.sourcePlaceholder': 'e.g., 192.168.1.0/24; empty: anywhere',
	'firewall.comment': 'Comment (optional)',
	'firewall.commentPlaceholder': 'What the rule is for',
	'firewall.addHint': {
		one: 'The rule is for incoming connections. While the firewall is on, the change is undone after {n} second unless you keep it.',
		other:
			'The rule is for incoming connections. While the firewall is on, the change is undone after {n} seconds unless you keep it.'
	},

	// A rule's direction and protocol in the list of rules; its action is the form's word.
	'firewall.in': 'IN',
	'firewall.out': 'OUT',
	'firewall.tcp': 'TCP',
	'firewall.udp': 'UDP',
	'firewall.anyProtocol': 'ANY',
	// The default policies in firewall.defaults.
	'firewall.policyAllow': 'allow',
	'firewall.policyDeny': 'deny',
	'firewall.policyReject': 'reject'
};

export const ru: Translation<typeof en> = {
	'firewall.title': 'Брандмауэр',
	'firewall.active': 'Активен',
	'firewall.inactive': 'Неактивен',
	'firewall.notInstalled': 'Не установлен',
	'firewall.configure': 'Настроить',
	'firewall.description':
		'Брандмауэр ufw хоста решает, какие компьютеры достучатся до служб NAS. Прежде чем включить его, UNOS разрешает локальным сетям доступ к веб-интерфейсу, SSH, SMB и iSCSI.',
	'firewall.missing':
		'На хосте не установлен ufw. Установите на хосте пакет ufw (sudo apt install ufw) и откройте эту страницу снова.',
	'firewall.loadFailed': 'Не удалось прочитать брандмауэр',
	'firewall.status': 'Состояние брандмауэра',
	'firewall.defaults': 'По умолчанию: входящие — {incoming}, исходящие — {outgoing}',
	'firewall.enable': 'Включить',
	'firewall.disable': 'Выключить',
	'firewall.reset': 'Сбросить',
	'firewall.rules': 'Правила брандмауэра',
	'firewall.addRule': 'Добавить правило',
	'firewall.noRules': 'На хосте нет правил брандмауэра',
	'firewall.port': 'Порт: {port}',
	'firewall.any': 'Любой',
	'firewall.from': 'Источник: {source}',
	'firewall.to': 'Назначение: {destination}',
	'firewall.unos': 'UNOS',
	'firewall.ipv6': 'IPv6',
	'firewall.route': 'Маршрутизация',
	'firewall.unosHint':
		'Свои правила, которые открывают локальным сетям веб-интерфейс, SSH, SMB, iSCSI и обнаружение в сети, UNOS сохраняет: удалить их нельзя.',
	'firewall.docker':
		'По IPv4 ufw не фильтрует порты, которые публикует Docker: порты веб-интерфейса и приложений в сетях Docker. По IPv6 фильтрует, как и порты приложений в сети хоста, например Home Assistant: для них нужны правила.',
	'firewall.deleteRule': 'Удалить правило {n}',
	'firewall.ruleNumber': 'Правило {n}',
	'firewall.enabledNotice': 'Брандмауэр включён',
	'firewall.alreadyOn': 'Брандмауэр уже включён',
	'firewall.disabledNotice': 'Брандмауэр выключен',
	'firewall.added': 'Правило добавлено',
	'firewall.deleted': 'Правило удалено',
	'firewall.resetDone': 'Брандмауэр сброшен к значениям по умолчанию: он выключен, правила UNOS на месте',
	'firewall.kept': 'Изменение брандмауэра сохранено',
	'firewall.toggleFailed': 'Не удалось переключить брандмауэр',
	'firewall.addFailed': 'Не удалось добавить правило',
	'firewall.deleteFailed': 'Не удалось удалить правило',
	'firewall.resetFailed': 'Не удалось сбросить брандмауэр',
	'firewall.keepFailed': 'Не удалось сохранить изменение',
	'firewall.confirmDelete': 'Удалить это правило из брандмауэра хоста?',
	'firewall.deleteAction': 'Удалить правило',
	'firewall.resetTitle': 'Сбросить брандмауэр?',
	'firewall.confirmReset': 'Он будет выключен, все правила удалены, а правила UNOS добавлены снова.',
	'firewall.resetAction': 'Сбросить брандмауэр',

	'firewall.enableTitle': 'Включить брандмауэр?',
	'firewall.enableRules':
		'Сначала UNOS добавит правила, которые открывают локальным сетям веб-интерфейс (порты 80 и 443), SSH, SMB (445), iSCSI (3260) и обнаружение в сети: {networks}.',
	'firewall.enableRevert': {
		one: 'Затем брандмауэр включится и через {n} секунду снова выключится, если вы не сохраните изменение. Прежде чем сохранить, проверьте с другого компьютера, что SSH и общие папки по-прежнему отвечают.',
		few: 'Затем брандмауэр включится и через {n} секунды снова выключится, если вы не сохраните изменение. Прежде чем сохранить, проверьте с другого компьютера, что SSH и общие папки по-прежнему отвечают.',
		many: 'Затем брандмауэр включится и через {n} секунд снова выключится, если вы не сохраните изменение. Прежде чем сохранить, проверьте с другого компьютера, что SSH и общие папки по-прежнему отвечают.'
	},
	'firewall.enableOthers':
		'Другим службам, например NFS, нужны свои правила; так же и компьютеру, который видит NAS только по публичному адресу IPv6.',
	'firewall.turnOn': 'Включить',
	'firewall.notLocal':
		'Этот компьютер не входит в локальные сети: когда брандмауэр включится, для SSH и SMB с него понадобится отдельное правило. Добавьте его заранее.',
	'firewall.working': 'Брандмауэр включается: на небольшой плате это занимает до минуты.',
	'firewall.reverting': 'Хост отменяет изменение брандмауэра: карточка проверит снова через несколько секунд.',
	'firewall.revertFailed':
		'Хосту не удалось отменить последнее изменение брандмауэра: проверьте правила ниже или выключите брандмауэр. Причина — в журнале хоста (journalctl -u unos-firewall-revert).',
	'firewall.on': 'Интерфейс: {interface}',

	'firewall.waiting': {
		one: 'Это изменение брандмауэра будет отменено через {n} секунду, если вы его не сохраните. Сначала проверьте, что SSH и общие папки по-прежнему отвечают.',
		few: 'Это изменение брандмауэра будет отменено через {n} секунды, если вы его не сохраните. Сначала проверьте, что SSH и общие папки по-прежнему отвечают.',
		many: 'Это изменение брандмауэра будет отменено через {n} секунд, если вы его не сохраните. Сначала проверьте, что SSH и общие папки по-прежнему отвечают.'
	},
	'firewall.keep': 'Сохранить изменение',
	'firewall.undone':
		'Время сохранить изменение брандмауэра истекло: если его не сохранили в другом месте, брандмауэр вернулся в прежнее состояние.',
	'firewall.notYet':
		'Изменение брандмауэра уже должно было быть отменено: откройте эту страницу снова, чтобы увидеть брандмауэр как есть.',

	'firewall.addTitle': 'Новое правило брандмауэра',
	'firewall.action': 'Действие',
	'firewall.allow': 'Разрешить',
	'firewall.deny': 'Запретить',
	'firewall.reject': 'Отклонить',
	'firewall.limit': 'Ограничить',
	'firewall.protocol': 'Протокол',
	'firewall.portLabel': 'Порт',
	'firewall.portPlaceholder': 'например, 8080, 6000:6007 или 80,443',
	'firewall.portRequired': 'Укажите порт',
	'firewall.source': 'Источник (необязательно)',
	'firewall.sourcePlaceholder': 'например, 192.168.1.0/24; пусто — откуда угодно',
	'firewall.comment': 'Комментарий (необязательно)',
	'firewall.commentPlaceholder': 'Для чего это правило',
	'firewall.addHint': {
		one: 'Правило для входящих соединений. Пока брандмауэр включён, изменение будет отменено через {n} секунду, если вы его не сохраните.',
		few: 'Правило для входящих соединений. Пока брандмауэр включён, изменение будет отменено через {n} секунды, если вы его не сохраните.',
		many: 'Правило для входящих соединений. Пока брандмауэр включён, изменение будет отменено через {n} секунд, если вы его не сохраните.'
	},

	'firewall.in': 'Входящие',
	'firewall.out': 'Исходящие',
	'firewall.tcp': 'TCP',
	'firewall.udp': 'UDP',
	'firewall.anyProtocol': 'Любой',
	'firewall.policyAllow': 'разрешены',
	'firewall.policyDeny': 'запрещены',
	'firewall.policyReject': 'отклоняются'
};
