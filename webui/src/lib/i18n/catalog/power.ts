import type { Translation } from '../translate';

// System > Power Management: src/routes/system/tabs/PowerTab.svelte. The host's answers to the
// actions are the server's messages and stay in English.

export const en = {
	'power.title': 'Power Management',
	'power.actions.title': 'Reboot, shutdown and suspend',
	'power.actions.hint':
		'Reboot and Shutdown act on the host one minute after you confirm, and you can cancel them here until then. Suspend acts at once.',

	'power.reboot.button': 'Reboot',
	'power.reboot.title': 'Reboot the host?',
	'power.reboot.text':
		'The host reboots one minute after you confirm; until then you can cancel the reboot here. Shares, apps and this page are unavailable until the host is back.',
	'power.reboot.confirm': 'Reboot in 1 minute',
	'power.reboot.failed': 'The reboot was not scheduled',
	'power.shutdown.button': 'Shutdown',
	'power.shutdown.title': 'Shut down the host?',
	'power.shutdown.text':
		'The host shuts down one minute after you confirm; until then you can cancel the shutdown here. It starts again with its power button, or with a Wake-on-LAN packet where the board supports that.',
	'power.shutdown.confirm': 'Shut down in 1 minute',
	'power.shutdown.failed': 'The shutdown was not scheduled',
	'power.suspend.button': 'Suspend',
	'power.suspend.title': 'Suspend the host?',
	'power.suspend.text':
		'The host suspends at once and sleeps until its power button or a Wake-on-LAN packet wakes it. Shares, apps and this page are unavailable meanwhile.',
	'power.suspend.confirm': 'Suspend now',
	'power.suspend.failed': 'The host was not suspended',

	'power.pending.readError': 'The pending reboot or shutdown could not be read',
	'power.pending.reboot': 'A reboot is pending',
	'power.pending.shutdown': 'A shutdown is pending',
	'power.pending.rebootAt': 'The host reboots at {time}.',
	'power.pending.rebootSoon': 'The host reboots within a minute.',
	'power.pending.shutdownAt': 'The host shuts down at {time}.',
	'power.pending.shutdownSoon': 'The host shuts down within a minute.',
	'power.pending.cancelReboot': 'Cancel reboot',
	'power.pending.cancelShutdown': 'Cancel shutdown',

	'power.wol.title': 'Wake-on-LAN',
	'power.wol.loadError': 'The network interfaces could not be read',
	'power.wol.none': 'The host has no Ethernet interface',
	'power.wol.interface': 'Interface',
	'power.wol.mac': 'MAC address',
	'power.wol.change': 'Change',
	'power.wol.unknownMac': 'unknown',
	'power.wol.notSupported': 'Not supported',
	'power.wol.on': 'On',
	'power.wol.off': 'Off',
	'power.wol.turnOn': 'Turn on',
	'power.wol.turnOff': 'Turn off',
	// After Turn on or Turn off, for screen readers: "Turn on Wake-on-LAN for eth0".
	'power.wol.forInterface': 'Wake-on-LAN for {name}',
	'power.wol.turnedOn': 'Wake-on-LAN is on for {name}',
	'power.wol.turnedOff': 'Wake-on-LAN is off for {name}',
	// The hint around the example command, power.wol.command.
	'power.wol.hintBefore':
		"With Wake-on-LAN on, a magic packet to the interface's MAC address, sent from a computer on the same network (for example",
	'power.wol.command': 'wakeonlan <MAC address>',
	'power.wol.hintAfter':
		'), starts the host after a shutdown and wakes it from suspend, but only where the board keeps the network card powered while the host is off: many single-board computers do not. UNOS sets your choice again whenever it starts.'
};

export const ru: Translation<typeof en> = {
	'power.title': 'Управление питанием',
	'power.actions.title': 'Перезагрузка, выключение и спящий режим',
	'power.actions.hint':
		'Перезагрузка и выключение выполняются через минуту после подтверждения, и до этого их можно отменить здесь. Спящий режим включается сразу.',

	'power.reboot.button': 'Перезагрузить',
	'power.reboot.title': 'Перезагрузить хост?',
	'power.reboot.text':
		'Хост перезагрузится через минуту после подтверждения; до этого перезагрузку можно отменить здесь. Общие папки, приложения и эта страница будут недоступны, пока хост не запустится снова.',
	'power.reboot.confirm': 'Перезагрузить через 1 минуту',
	'power.reboot.failed': 'Перезагрузка не запланирована',
	'power.shutdown.button': 'Выключить',
	'power.shutdown.title': 'Выключить хост?',
	'power.shutdown.text':
		'Хост выключится через минуту после подтверждения; до этого выключение можно отменить здесь. Снова включить хост можно кнопкой питания или пакетом Wake-on-LAN, если плата это поддерживает.',
	'power.shutdown.confirm': 'Выключить через 1 минуту',
	'power.shutdown.failed': 'Выключение не запланировано',
	'power.suspend.button': 'Спящий режим',
	'power.suspend.title': 'Перевести хост в спящий режим?',
	'power.suspend.text':
		'Хост сразу перейдёт в спящий режим и проснётся от кнопки питания или пакета Wake-on-LAN. До этого общие папки, приложения и эта страница недоступны.',
	'power.suspend.confirm': 'Перевести в спящий режим',
	'power.suspend.failed': 'Хост не переведён в спящий режим',

	'power.pending.readError': 'Не удалось узнать о запланированной перезагрузке или выключении',
	'power.pending.reboot': 'Запланирована перезагрузка',
	'power.pending.shutdown': 'Запланировано выключение',
	'power.pending.rebootAt': 'Хост перезагрузится в {time}.',
	'power.pending.rebootSoon': 'Хост перезагрузится в течение минуты.',
	'power.pending.shutdownAt': 'Хост выключится в {time}.',
	'power.pending.shutdownSoon': 'Хост выключится в течение минуты.',
	'power.pending.cancelReboot': 'Отменить перезагрузку',
	'power.pending.cancelShutdown': 'Отменить выключение',

	'power.wol.title': 'Wake-on-LAN',
	'power.wol.loadError': 'Не удалось прочитать сетевые интерфейсы',
	'power.wol.none': 'У хоста нет интерфейса Ethernet',
	'power.wol.interface': 'Интерфейс',
	'power.wol.mac': 'MAC-адрес',
	'power.wol.change': 'Изменить',
	'power.wol.unknownMac': 'неизвестен',
	'power.wol.notSupported': 'Не поддерживается',
	'power.wol.on': 'Включён',
	'power.wol.off': 'Выключен',
	'power.wol.turnOn': 'Включить',
	'power.wol.turnOff': 'Выключить',
	'power.wol.forInterface': 'Wake-on-LAN для {name}',
	'power.wol.turnedOn': 'Wake-on-LAN включён для {name}',
	'power.wol.turnedOff': 'Wake-on-LAN выключен для {name}',
	'power.wol.hintBefore':
		'Когда Wake-on-LAN включён, «магический» пакет на MAC-адрес интерфейса, отправленный с компьютера в той же сети (например,',
	'power.wol.command': 'wakeonlan <MAC-адрес>',
	'power.wol.hintAfter':
		'), включает хост после выключения и будит его из спящего режима, но только если плата не отключает питание сетевой карты, пока хост выключен: многие одноплатные компьютеры его отключают. UNOS заново применяет ваш выбор при каждом запуске.'
};
