import type { Translation } from '../translate';

// System > Remote access (src/routes/system/tabs/RemoteAccessTab.svelte): the WireGuard tunnel on
// the host, which reaches the NAS alone, and the local names of apps through the web UI's Caddy
// (ADR-0420).

export const en = {
	'remote.tab': 'Remote Access',

	'remote.title': 'Remote access (WireGuard)',
	'remote.on': 'On',
	'remote.off': 'Off',
	'remote.down': 'Down',
	'remote.notInstalled': 'Not installed',
	'remote.description':
		"A WireGuard tunnel lets your own devices reach the NAS from outside the LAN: the web UI, the shares, SSH and the apps' ports at the NAS's address in the tunnel, and no other computer of your LAN.",
	// One line of help under the title; the description and the router's part on demand.
	'remote.summary': 'Your own devices reach the NAS from outside the LAN through a WireGuard tunnel.',
	'remote.more': 'How it works',
	// A peer whose last handshake is less than 3 minutes old.
	'remote.online': 'Connected',
	'remote.missing':
		'wireguard-tools is not installed on the host. Install the package wireguard-tools on the host (sudo apt install wireguard-tools), then open this page again.',
	'remote.loadFailed': 'Remote access could not be read',
	'remote.endpoint': 'Public address',
	'remote.endpointHint':
		"Your router's public IP address or a dynamic DNS name, which your devices connect to from outside",
	'remote.endpointNeeded': 'Enter the public address that your devices connect to.',
	'remote.endpointPlaceholder': 'nas.example.org',
	'remote.port': 'UDP port',
	'remote.portHint': 'Your router forwards this UDP port to the NAS',
	'remote.network': 'Tunnel network',
	'remote.networkHint': 'Private IPv4 addresses of the tunnel, apart from the networks of your LAN',
	'remote.turnOn': 'Turn on',
	'remote.turnOff': 'Turn off',
	'remote.enableTitle': 'Turn remote access on?',
	'remote.enableNasOnly':
		"The devices that you add as peers reach the NAS alone through the tunnel: the web UI, the shares, SSH and the apps' ports. They reach no other computer of your LAN.",
	'remote.enableRouter':
		"To connect from outside, forward UDP port {port} on your router to the NAS's address in the LAN. Your devices connect to {endpoint}.",
	'remote.enableFirewall': 'When the host has ufw, UNOS lets UDP port {port} through it.',
	'remote.enableLan': 'Turning remote access on or off does not change how the LAN reaches the NAS.',
	'remote.turnedOn': 'Remote access is on',
	'remote.disableTitle': 'Turn remote access off?',
	'remote.disableText':
		"Every peer's tunnel ends, also the one this page may use now. The peers stay listed, and their configurations work again once remote access is on again.",
	'remote.turnedOff': 'Remote access is off',
	'remote.downWarning':
		'Remote access is on, but the tunnel is not up on the host. Turn it off and on again, or read the unit wg-quick@unos-wg in System > Logs.',
	'remote.firewallMissing':
		"The host's firewall does not let the tunnel's port in. Turn remote access off and on again.",
	'remote.address': "NAS's address in the tunnel",
	'remote.publicKey': "Server's public key",
	'remote.changeEndpoint': 'Change the public address',
	'remote.endpointTitle': 'Change the public address',
	'remote.endpointNote':
		"New peers' configurations name it; devices that have a configuration keep the address it names.",
	'remote.save': 'Save',
	'remote.endpointSaved': 'The public address was saved',
	'remote.peers': 'Peers',
	'remote.addPeer': 'Add peer',
	'remote.noPeers': 'No device may connect yet: add a peer for each of your devices.',
	'remote.handshake': 'Last connected: {time}',
	'remote.neverConnected': 'Not connected yet',
	'remote.traffic': 'Received {received}, sent {sent}',
	'remote.revoke': 'Revoke',
	'remote.revokeLabel': 'Revoke {name}',
	'remote.revokeTitle': 'Revoke {name}?',
	'remote.revokeText': '{name} can no longer connect, and its tunnel ends at once.',
	'remote.revoked': '{name} was revoked',
	'remote.addTitle': 'Add a peer',
	'remote.peerName': 'Device name',
	'remote.peerNamePlaceholder': 'phone',
	'remote.publicKeyLabel': "Device's public key (optional)",
	'remote.publicKeyHint':
		'Leave it empty and UNOS makes the key pair. Give the public key of a device that made its own, and the configuration holds no private key.',
	'remote.configTitle': 'Configuration of {name}',
	'remote.configOnce':
		"This configuration is shown only now: UNOS keeps no copy of the device's private key. Scan the QR code with the WireGuard app, or download the file and import it.",
	'remote.configOwnKey': "Put the device's own private key into this configuration before you import it.",
	'remote.qrAlt': 'QR code of the WireGuard configuration of {name}',
	'remote.download': 'Download',
	'remote.done': 'Done',
	'remote.outside':
		"From outside the LAN: your router forwards UDP port {port} to the NAS, and each device imports its peer's configuration into the WireGuard app. Behind a carrier-grade NAT (no public IPv4 address) no port forward reaches the NAS.",
	'remote.size.bytes': 'B',
	'remote.size.kilobytes': 'KB',
	'remote.size.megabytes': 'MB',
	'remote.size.gigabytes': 'GB',
	'remote.size.terabytes': 'TB',

	'remote.names.title': 'Local names for apps',
	'remote.names.description':
		"An app with a name opens at its own address on the LAN, such as https://jellyfin-{host}.local, over HTTPS from the web UI's certificate authority. The app's own port keeps working.",
	'remote.names.loadFailed': 'The app names could not be read',
	'remote.names.noVolume':
		"The web UI cannot read the app names yet: the volume app-names is not mounted. Deploy UNOS's compose file again.",
	'remote.names.noAnnouncer':
		'avahi-utils is not installed on the host, which announces the names on the LAN. Install the package avahi-utils on the host (sudo apt install avahi-utils), then open this page again.',
	'remote.names.none': 'No app has a name yet.',
	'remote.names.add': 'Add name',
	'remote.names.addTitle': 'Give an app a name',
	'remote.names.app': 'App',
	'remote.names.noApps': 'No installed app publishes a TCP port.',
	'remote.names.label': 'Name',
	'remote.names.labelHint': '1 to 32 characters a-z, 0-9 and -. The app opens at https://{name}-{host}.local',
	'remote.names.added': '{name} now opens the app',
	'remote.names.remove': 'Remove',
	'remote.names.removeLabel': 'Remove the name {name}',
	'remote.names.removeTitle': 'Remove the name {name}?',
	'remote.names.removed': 'The name {name} was removed',
	'remote.names.port': '{app}, port {port}',
	'remote.names.missingApp': 'The app is not installed, or publishes another port now',
	'remote.names.addressChanged':
		'avahi announces the names with {announced}, but the host has the address {address} now.',
	'remote.names.apply': 'Announce again',
	'remote.names.applied': 'The names are announced with the address of now',
	'remote.names.trust':
		"A browser that trusts the certificate authority of the web UI trusts the names too; otherwise it warns once for each name. The names reach the LAN only: through the tunnel, open the app's port at the NAS's address in the tunnel."
};

export const ru: Translation<typeof en> = {
	'remote.tab': 'Удалённый доступ',

	'remote.title': 'Удалённый доступ (WireGuard)',
	'remote.on': 'Включён',
	'remote.off': 'Выключен',
	'remote.down': 'Не работает',
	'remote.notInstalled': 'Не установлен',
	'remote.description':
		'Туннель WireGuard позволяет вашим устройствам подключаться к NAS извне локальной сети: к веб-интерфейсу, общим папкам, SSH и портам приложений по адресу NAS в туннеле, и больше ни к одному компьютеру вашей сети.',
	'remote.summary': 'Ваши устройства подключаются к NAS извне локальной сети через туннель WireGuard.',
	'remote.more': 'Как это работает',
	'remote.online': 'Подключён',
	'remote.missing':
		'На хосте не установлен wireguard-tools. Установите на хосте пакет wireguard-tools (sudo apt install wireguard-tools) и снова откройте эту страницу.',
	'remote.loadFailed': 'Не удалось прочитать настройки удалённого доступа',
	'remote.endpoint': 'Внешний адрес',
	'remote.endpointHint':
		'Внешний IP-адрес вашего маршрутизатора или имя динамического DNS, к которому устройства подключаются извне',
	'remote.endpointNeeded': 'Введите внешний адрес, к которому подключаются ваши устройства.',
	'remote.endpointPlaceholder': 'nas.example.org',
	'remote.port': 'UDP-порт',
	'remote.portHint': 'Маршрутизатор перенаправляет этот UDP-порт на NAS',
	'remote.network': 'Сеть туннеля',
	'remote.networkHint': 'Частные IPv4-адреса туннеля, не пересекающиеся с сетями вашей локальной сети',
	'remote.turnOn': 'Включить',
	'remote.turnOff': 'Выключить',
	'remote.enableTitle': 'Включить удалённый доступ?',
	'remote.enableNasOnly':
		'Устройства, добавленные как узлы, получают через туннель доступ только к NAS: к веб-интерфейсу, общим папкам, SSH и портам приложений. Другие компьютеры вашей сети им недоступны.',
	'remote.enableRouter':
		'Чтобы подключаться извне, перенаправьте на маршрутизаторе UDP-порт {port} на адрес NAS в локальной сети. Устройства подключаются к {endpoint}.',
	'remote.enableFirewall': 'Если на хосте есть ufw, UNOS открывает в нём UDP-порт {port}.',
	'remote.enableLan': 'Включение и выключение удалённого доступа не меняет доступ к NAS из локальной сети.',
	'remote.turnedOn': 'Удалённый доступ включён',
	'remote.disableTitle': 'Выключить удалённый доступ?',
	'remote.disableText':
		'Туннели всех узлов закроются, в том числе тот, через который, возможно, открыта эта страница. Узлы останутся в списке, и их конфигурации снова заработают, когда удалённый доступ будет включён.',
	'remote.turnedOff': 'Удалённый доступ выключен',
	'remote.downWarning':
		'Удалённый доступ включён, но туннель на хосте не поднят. Выключите и снова включите удалённый доступ или посмотрите журнал юнита wg-quick@unos-wg в разделе «Система» → «Системные журналы».',
	'remote.firewallMissing': 'Брандмауэр хоста не пропускает порт туннеля. Выключите и снова включите удалённый доступ.',
	'remote.address': 'Адрес NAS в туннеле',
	'remote.publicKey': 'Открытый ключ сервера',
	'remote.changeEndpoint': 'Изменить внешний адрес',
	'remote.endpointTitle': 'Изменение внешнего адреса',
	'remote.endpointNote':
		'Его получат конфигурации новых узлов; у устройств с уже выданной конфигурацией останется прежний адрес.',
	'remote.save': 'Сохранить',
	'remote.endpointSaved': 'Внешний адрес сохранён',
	'remote.peers': 'Узлы',
	'remote.addPeer': 'Добавить узел',
	'remote.noPeers': 'Пока ни одно устройство не может подключиться: добавьте узел для каждого устройства.',
	'remote.handshake': 'Последнее подключение: {time}',
	'remote.neverConnected': 'Ещё не подключался',
	'remote.traffic': 'Получено {received}, отправлено {sent}',
	'remote.revoke': 'Отозвать',
	'remote.revokeLabel': 'Отозвать {name}',
	'remote.revokeTitle': 'Отозвать {name}?',
	'remote.revokeText': '{name} больше не сможет подключиться, а его туннель сразу закроется.',
	'remote.revoked': 'Узел {name} отозван',
	'remote.addTitle': 'Добавление узла',
	'remote.peerName': 'Имя устройства',
	'remote.peerNamePlaceholder': 'телефон',
	'remote.publicKeyLabel': 'Открытый ключ устройства (необязательно)',
	'remote.publicKeyHint':
		'Оставьте пустым, и UNOS создаст пару ключей. Укажите открытый ключ устройства, создавшего свои ключи, и в конфигурации не будет закрытого ключа.',
	'remote.configTitle': 'Конфигурация {name}',
	'remote.configOnce':
		'Эта конфигурация показывается только сейчас: UNOS не хранит копию закрытого ключа устройства. Отсканируйте QR-код приложением WireGuard или скачайте файл и импортируйте его.',
	'remote.configOwnKey': 'Перед импортом добавьте в эту конфигурацию собственный закрытый ключ устройства.',
	'remote.qrAlt': 'QR-код конфигурации WireGuard для {name}',
	'remote.download': 'Скачать',
	'remote.done': 'Готово',
	'remote.outside':
		'Извне локальной сети: маршрутизатор перенаправляет UDP-порт {port} на NAS, а каждое устройство импортирует конфигурацию своего узла в приложение WireGuard. За NAT оператора (без внешнего IPv4-адреса) перенаправление портов до NAS не доходит.',
	'remote.size.bytes': 'Б',
	'remote.size.kilobytes': 'КБ',
	'remote.size.megabytes': 'МБ',
	'remote.size.gigabytes': 'ГБ',
	'remote.size.terabytes': 'ТБ',

	'remote.names.title': 'Локальные имена приложений',
	'remote.names.description':
		'Приложение с именем открывается в локальной сети по собственному адресу, например https://jellyfin-{host}.local, по HTTPS с сертификатом от центра сертификации веб-интерфейса. Собственный порт приложения продолжает работать.',
	'remote.names.loadFailed': 'Не удалось прочитать имена приложений',
	'remote.names.noVolume':
		'Веб-интерфейс пока не может читать имена приложений: том app-names не подключён. Разверните compose-файл UNOS заново.',
	'remote.names.noAnnouncer':
		'На хосте не установлен avahi-utils, который объявляет имена в локальной сети. Установите на хосте пакет avahi-utils (sudo apt install avahi-utils) и снова откройте эту страницу.',
	'remote.names.none': 'Пока ни у одного приложения нет имени.',
	'remote.names.add': 'Добавить имя',
	'remote.names.addTitle': 'Имя для приложения',
	'remote.names.app': 'Приложение',
	'remote.names.noApps': 'Ни одно установленное приложение не публикует TCP-порт.',
	'remote.names.label': 'Имя',
	'remote.names.labelHint':
		'От 1 до 32 символов a-z, 0-9 и -. Приложение откроется по адресу https://{name}-{host}.local',
	'remote.names.added': 'Теперь приложение открывается по имени {name}',
	'remote.names.remove': 'Удалить',
	'remote.names.removeLabel': 'Удалить имя {name}',
	'remote.names.removeTitle': 'Удалить имя {name}?',
	'remote.names.removed': 'Имя {name} удалено',
	'remote.names.port': '{app}, порт {port}',
	'remote.names.missingApp': 'Приложение не установлено или теперь публикует другой порт',
	'remote.names.addressChanged': 'avahi объявляет имена с адресом {announced}, но у хоста теперь адрес {address}.',
	'remote.names.apply': 'Объявить заново',
	'remote.names.applied': 'Имена объявлены с текущим адресом',
	'remote.names.trust':
		'Браузер, доверяющий центру сертификации веб-интерфейса, доверяет и именам; иначе он один раз предупредит о каждом имени. Имена работают только в локальной сети: через туннель открывайте порт приложения по адресу NAS в туннеле.'
};
