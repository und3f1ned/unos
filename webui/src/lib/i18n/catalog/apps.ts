import type { Translation } from '../translate';

// The App Store: src/routes/apps/+page.svelte and src/lib/components/apps/. The names and the
// descriptions of the apps and of their variables, and their memory recommendations, come from
// the server's catalog and are shown as it answers them (ADR-0291).

export const en = {
	'apps.title': 'App Store',
	'apps.summary': '{available} apps available, {installed} installed',
	'apps.summaryAvailable': '{available} apps available',
	'apps.catalogLoadError': 'The app catalog could not be loaded: {error}',
	'apps.tabs.catalog': 'Catalog',
	'apps.tabs.installed': 'Installed ({count})',
	'apps.tabs.installedUnknown': 'Installed',
	'apps.loading': 'Loading apps...',
	'apps.featured': 'Featured Apps',
	'apps.categories': 'Categories',
	'apps.search': 'Search apps...',
	'apps.noneFound': 'No apps found',
	'apps.noneFoundHint': 'Try adjusting your search or category filter',
	'apps.clearFilters': 'Clear Filters',
	'apps.noneInstalled': 'No apps installed',
	'apps.noneInstalledHint': 'Install apps from the catalog to get started',
	'apps.browseCatalog': 'Browse Catalog',
	'apps.install': 'Install',
	'apps.open': 'Open',
	'apps.installedNotice': '{name} was installed.',
	'apps.installedNoticePool': '{name} was installed. Its data is stored on the "{pool}" pool.',
	// The notice of the install of an app whose first account to register becomes its administrator.
	'apps.install.firstAdmin':
		'Open {name} now and create its administrator: the first account that registers becomes the administrator.',
	// The states of an installed app: its main container runs, or is in any other state; an app
	// that lacks one of its containers is incomplete (ADR-0381).
	'apps.state.running': 'Running',
	'apps.state.stopped': 'Stopped',
	'apps.state.incomplete': 'Incomplete',
	// Docker's health of an installed app's main container, by the word Docker answers.
	'apps.health.starting': 'Starting',
	'apps.health.healthy': 'Healthy',
	'apps.health.unhealthy': 'Unhealthy',

	// The categories of AppCategory (supervisor/src/apps/schemas.py), as the category list names
	// them; all is every app.
	'apps.category.all': 'All Apps',
	'apps.category.homeAutomation': 'Smart Home',
	'apps.category.media': 'Media',
	'apps.category.ai': 'AI & LLMs',
	'apps.category.productivity': 'Productivity',
	'apps.category.download': 'Downloads',
	'apps.category.backup': 'Backup',
	'apps.category.monitoring': 'Monitoring',
	'apps.category.network': 'Network',
	'apps.category.documents': 'Documents',
	'apps.category.development': 'Development',
	'apps.category.security': 'Security',
	'apps.category.communication': 'Communication',
	'apps.category.privacy': 'Privacy',
	'apps.category.gaming': 'Gaming',
	'apps.category.other': 'Other',

	// The sources of the images (AppSource): linuxserver.io is LinuxServer on a card and
	// LinuxServer.io in the details.
	'apps.source.official': 'Official',
	'apps.source.linuxserver': 'LinuxServer',
	'apps.source.linuxserverIo': 'LinuxServer.io',
	'apps.source.verified': 'Verified',
	'apps.source.community': 'Community',

	'apps.installed.port': 'Port:',
	'apps.installed.pool': 'Data on pool:',
	// Followed by the name of the group, unos-smb.
	'apps.installed.inGroup': 'Runs in the group',
	// Followed by the names of the containers of an app of several containers (ADR-0381).
	'apps.installed.containers': 'Containers:',
	// {containers} is a list of the names of containers.
	'apps.installed.missing':
		'{name} is incomplete: Docker lacks {containers}. It cannot start: uninstall it (its data folders are kept) and install it again.',
	'apps.installed.companionStopped':
		'{name} runs without {containers}. Restart {name} to start all its containers; the Containers page shows the log of one that keeps stopping.',
	'apps.installed.start': 'Start',
	'apps.installed.stop': 'Stop',
	'apps.installed.restart': 'Restart',
	'apps.installed.uninstall': 'Uninstall',
	// {n} is the number of the app's containers that Docker has.
	'apps.installed.confirmUninstall': {
		one: 'Uninstall {name}? This removes its container and keeps its data folders.',
		other: 'Uninstall {name}? This removes its {n} containers and keeps its data folders.'
	},
	'apps.installed.loadError': 'The installed apps could not be loaded: {error}',
	'apps.installed.startFailed': 'Start failed',
	'apps.installed.stopFailed': 'Stop failed',
	'apps.installed.restartFailed': 'Restart failed',
	'apps.installed.uninstallFailed': 'Uninstall failed',

	// The data folders of an installed app that are not on a mounted pool.
	'apps.storage.unavailableTitle': 'Data folders are not available',
	'apps.storage.unavailable':
		'Some data folders of {name} are not on a mounted storage pool. Mount the pool that holds them on the Storage page, then start the app.',

	'apps.detail.installed': 'Installed',
	'apps.detail.website': 'Website',
	'apps.detail.github': 'GitHub',
	'apps.detail.docs': 'Docs',
	'apps.detail.loadingPools': 'Loading storage pools...',
	'apps.detail.poolsError': 'Could not load storage pools',
	'apps.detail.noPoolTitle': 'No mounted storage pool',
	'apps.detail.noPool':
		'{name} keeps its data on a storage pool, and no pool is mounted. Create or mount a pool on the Storage page, then install the app.',
	'apps.detail.goToStorage': 'Go to Storage',
	'apps.detail.pool': 'Storage pool',
	'apps.detail.poolHint': "The app's data is stored on this pool.",
	'apps.detail.poolOption': '{name} — {free} free of {total}',
	'apps.detail.unosSmb': 'Run in the group unos-smb',
	'apps.detail.unosSmbHint':
		"Sets PUID to {puid}, PGID to {pgid} (unos-smb) and UMASK to 007. Only an app in this group can be given a share's folder on the Volumes tab: it reads and changes every file there, and SMB users can change what it writes.",
	'apps.detail.unosSmbWarning':
		'{name} can read and change the files of every share folder you give it. If it can be reached from the internet, whoever takes it over gets those files too.',
	'apps.detail.runsInUnosSmb':
		'{name} runs in the group unos-smb (PUID {puid}, PGID {pgid}): it reads and changes the files of the share folders it was given.',
	// The hardware of the host's SoC that an app's containers get (ADR-0382): the badge of its card,
	// and of the card of an installed app a container of which did not get it, the hint's title,
	// what a hardware profile gives (by its id; generic for one without a text of its own), what to
	// choose in the app (by the app's id and the profile's) and the warning for an installed app a
	// container of which did not get it, in its details and in the notice of its install. The menus
	// of Jellyfin and Immich are named as in their English interfaces.
	'apps.hardware.badge': 'Hardware acceleration',
	'apps.hardware.badgeMissing': 'No hardware acceleration',
	'apps.hardware.title': 'Hardware acceleration',
	'apps.hardware.generic': 'UNOS installs {name} with the hardware of this NAS that the app uses.',
	'apps.hardware.profile.rk3588Video':
		"UNOS installs {name} with the video hardware of this NAS's Rockchip RK3588 that its kernel provides: the video decoders and encoders (MPP), the RGA and the GPU, in a container without privileged mode.",
	'apps.hardware.profile.rk3588Npu':
		"UNOS installs {name}'s machine learning with the NPU of this NAS's Rockchip RK3588: smart search, face recognition and text recognition run on it, in a container without privileged mode.",
	'apps.hardware.missing':
		'{name} was installed without the hardware of this NAS that it can use, so hardware acceleration does not work in it. To give it the hardware, uninstall {name} (its data folders are kept) and install it again on the same pool.',
	'apps.hardware.setting.jellyfin.rk3588Video':
		'In Jellyfin, choose Dashboard > Playback > Transcoding > Hardware acceleration: Rockchip MPP (RKMPP), and the codecs to decode in hardware.',
	'apps.hardware.setting.immich.rk3588Video':
		'To transcode videos in hardware, choose RKMPP in Immich under Administration > Settings > Video Transcoding Settings > Hardware Acceleration.',
	'apps.detail.tabs.overview': 'Overview',
	'apps.detail.tabs.ports': 'Ports ({count})',
	'apps.detail.tabs.volumes': 'Volumes ({count})',
	'apps.detail.tabs.environment': 'Environment ({count})',
	'apps.detail.image': 'Docker Image',
	// The containers of an app of several containers (ADR-0381), each with its image.
	'apps.detail.containers': 'Containers',
	'apps.detail.containersHint':
		'{name} runs in {count} containers on a network of its own: UNOS installs, starts, stops and uninstalls them together. Each image is pinned by its digest.',
	// An app whose database keeps the pool's disks awake while it runs (ADR-0381).
	'apps.detail.diskAwake':
		'While {name} runs, the hard disks of its pool do not go to sleep: its database and its jobs keep writing to them. Stop {name} on the Installed tab to let them sleep.',
	'apps.detail.dataLocations': 'Data locations on {pool}',
	'apps.detail.mountedAt': 'mounted at {path}',
	'apps.detail.mountedAtReadOnly': 'mounted at {path} (read-only)',
	// The same in one of the containers of an app of several.
	'apps.detail.mountedAtIn': 'mounted at {path} in {container}',
	'apps.detail.mountedAtInReadOnly': 'mounted at {path} in {container} (read-only)',
	// A password file that UNOS generates at the install (ADR-0381).
	'apps.detail.generatedSecret': 'A password that UNOS generates at the install and keeps in this file; it is never shown.',
	'apps.detail.resources': 'Resource Recommendations',
	'apps.detail.minMemory': 'Minimum Memory',
	'apps.detail.recommendedMemory': 'Recommended Memory',
	'apps.detail.ports': 'Ports',
	'apps.detail.volumes': 'Volumes',
	'apps.detail.variables': 'Variables',
	'apps.detail.portsHint': 'Configure port mappings. Modify host ports if they conflict with existing services.',
	'apps.detail.portsHostNetwork':
		"{name} runs in the host's network: it listens on these ports of the host itself, so they cannot be changed.",
	'apps.detail.hostPort': 'Host Port',
	'apps.detail.containerPort': 'Container Port',
	'apps.detail.portRange': 'Use a port from 1 to 65535',
	'apps.detail.installedVolumesHint': 'Host folders this installed app uses.',
	'apps.detail.hostPath': 'Host Path',
	'apps.detail.containerPath': 'Container Path',
	'apps.detail.readOnly': 'RO',
	'apps.detail.readWrite': 'RW',
	'apps.detail.notOnPool': 'Not on a pool',
	'apps.detail.noVolumes': 'No host folders are mounted into this app.',
	'apps.detail.volumesPoolHint':
		"Data folders are created on the selected storage pool. A data folder can point to another folder inside a mounted pool, never into a snapshot or the trash, and to a share's folder only for an app that runs in the group unos-smb.",
	'apps.detail.volumesSystemHint': 'System paths marked "System" are mounted from the host as they are.',
	'apps.detail.insidePool': 'Must be inside a mounted storage pool',
	'apps.detail.system': 'System',
	'apps.detail.file': 'File',
	'apps.detail.environmentHint':
		'Configure environment variables. Required variables must be set before installation.',
	'apps.detail.secretPlaceholder': 'Choose a unique value',
	'apps.detail.optional': 'Optional',
	'apps.detail.enter': 'Enter {name}',
	'apps.detail.required': 'Required',
	// The hints of PUID and PGID, which UNOS sets (ADR-0274).
	'apps.detail.setByUnos': 'Set by UNOS.',
	'apps.detail.setByUnosIn': 'Set by UNOS: {name} runs in the group unos-smb.',
	'apps.detail.setByUnosOutside': 'Set by UNOS: {name} runs outside the group unos-smb.',
	'apps.detail.setByUnosOption': 'Set by UNOS: {id} with "Run in the group unos-smb", otherwise {value}.',
	'apps.detail.setByUnosOptionGroup':
		'Set by UNOS: {id} (unos-smb) with "Run in the group unos-smb", otherwise {value}.',
	// What keeps the form from installing; {names} and {paths} are lists.
	'apps.detail.missingVariable': 'Set the required variable {names}.',
	'apps.detail.missingVariables': 'Set the required variables {names}.',
	'apps.detail.invalidPorts': 'Host ports must be numbers between 1 and 65535.',
	'apps.detail.invalidVolumes': 'Paths for {paths} must be inside a mounted storage pool.',
	'apps.detail.installFailed': 'Installation failed',
	'apps.detail.alreadyInstalled': 'Already Installed',
	'apps.detail.install': 'Install {name}',

	// A size that the page writes itself, by powers of 1024: {size} is the number.
	'apps.size.bytes': '{size} B',
	'apps.size.kb': '{size} KB',
	'apps.size.mb': '{size} MB',
	'apps.size.gb': '{size} GB',
	'apps.size.tb': '{size} TB',

	// An install that runs in the background (ADR-0384): what the details dialog says while the
	// job checks, and why it offers no install while another runs; then the card of each job on
	// the App Store: its name for screen readers, its title, its state, the step it reached, each
	// image's pull (by its state, its download for screen readers, its bytes and layers) and each
	// container (by its state), and Dismiss of a job that ended.
	'apps.detail.checking': 'Checking the pool, the ports and the free space...',
	'apps.detail.installRunning': 'An install of {name} is under way: install this app once it has ended.',
	'apps.job.label': 'Install of {name}',
	'apps.job.state.running': 'Installing',
	'apps.job.state.succeeded': 'Installed',
	'apps.job.state.failed': 'Failed',
	'apps.job.step.checking': 'Checking the pool, the ports and the free space',
	'apps.job.step.pulling': 'Downloading the images',
	'apps.job.step.creating': 'Creating the folders and containers',
	'apps.job.step.starting': 'Starting the containers',
	'apps.job.images': 'Images',
	'apps.job.containers': 'Containers',
	'apps.job.image.waiting': 'Waiting',
	'apps.job.image.present': 'Already on the NAS',
	'apps.job.image.pulling': 'Downloading',
	'apps.job.image.pulled': 'Downloaded',
	'apps.job.image.failed': 'Failed',
	'apps.job.download': 'Download of {image}',
	'apps.job.bytes': '{done} of {total}',
	'apps.job.layers': { one: '{done} of {n} layer', other: '{done} of {n} layers' },
	'apps.job.container.waiting': 'Waiting',
	'apps.job.container.created': 'Created',
	'apps.job.container.starting': 'Starting',
	'apps.job.container.running': 'Running',
	'apps.job.container.failed': 'Failed',
	'apps.job.container.removed': 'Removed',
	'apps.job.dismiss': 'Dismiss',
	// A running install can be cancelled from its card, after a question; the clean-up is a step of
	// its own; and the details say when the supervisor restarted while they waited for the checks.
	'apps.job.state.cancelled': 'Cancelled',
	'apps.job.step.cleaning': 'Removing what the install made',
	'apps.job.cancel': 'Cancel install',
	'apps.job.cancelling': 'Cancelling...',
	'apps.job.confirmCancel':
		'Cancel the install of {name}? What it made so far is removed; its data folders and the downloaded images are kept.',
	'apps.job.cancelledTitle': 'The install was cancelled',
	'apps.detail.jobLost':
		'UNOS restarted during the install, and its progress was lost. The Installed tab shows what it made.'
};

export const ru: Translation<typeof en> = {
	'apps.title': 'Магазин приложений',
	'apps.summary': 'Доступно приложений: {available}, установлено: {installed}',
	'apps.summaryAvailable': 'Доступно приложений: {available}',
	'apps.catalogLoadError': 'Не удалось загрузить каталог приложений: {error}',
	'apps.tabs.catalog': 'Каталог',
	'apps.tabs.installed': 'Установленные ({count})',
	'apps.tabs.installedUnknown': 'Установленные',
	'apps.loading': 'Загрузка приложений...',
	'apps.featured': 'Рекомендуемые приложения',
	'apps.categories': 'Категории',
	'apps.search': 'Поиск приложений...',
	'apps.noneFound': 'Приложения не найдены',
	'apps.noneFoundHint': 'Измените запрос или выберите другую категорию',
	'apps.clearFilters': 'Сбросить фильтры',
	'apps.noneInstalled': 'Нет установленных приложений',
	'apps.noneInstalledHint': 'Установите приложения из каталога, чтобы начать',
	'apps.browseCatalog': 'Перейти в каталог',
	'apps.install': 'Установить',
	'apps.open': 'Открыть',
	'apps.installedNotice': 'Приложение {name} установлено.',
	'apps.installedNoticePool': 'Приложение {name} установлено. Его данные хранятся в пуле «{pool}».',
	'apps.install.firstAdmin':
		'Откройте {name} сейчас и создайте его администратора: администратором становится первая зарегистрированная учётная запись.',
	'apps.state.running': 'Работает',
	'apps.state.stopped': 'Остановлено',
	'apps.state.incomplete': 'Неполное',
	'apps.health.starting': 'Запускается',
	'apps.health.healthy': 'Исправно',
	'apps.health.unhealthy': 'Неисправно',

	'apps.category.all': 'Все приложения',
	'apps.category.homeAutomation': 'Умный дом',
	'apps.category.media': 'Медиа',
	'apps.category.ai': 'ИИ и LLM',
	'apps.category.productivity': 'Продуктивность',
	'apps.category.download': 'Загрузки',
	'apps.category.backup': 'Резервное копирование',
	'apps.category.monitoring': 'Мониторинг',
	'apps.category.network': 'Сеть',
	'apps.category.documents': 'Документы',
	'apps.category.development': 'Разработка',
	'apps.category.security': 'Безопасность',
	'apps.category.communication': 'Общение',
	'apps.category.privacy': 'Конфиденциальность',
	'apps.category.gaming': 'Игры',
	'apps.category.other': 'Другое',

	'apps.source.official': 'Официальный',
	'apps.source.linuxserver': 'LinuxServer',
	'apps.source.linuxserverIo': 'LinuxServer.io',
	'apps.source.verified': 'Проверенный',
	'apps.source.community': 'Сообщество',

	'apps.installed.port': 'Порт:',
	'apps.installed.pool': 'Данные в пуле:',
	'apps.installed.inGroup': 'Работает в группе',
	'apps.installed.containers': 'Контейнеры:',
	'apps.installed.missing':
		'Приложение {name} неполное: в Docker нет {containers}. Его нельзя запустить: удалите его (папки с данными останутся) и установите снова.',
	'apps.installed.companionStopped':
		'Приложение {name} работает без {containers}. Перезапустите {name}, чтобы запустить все его контейнеры; журнал контейнера, который снова останавливается, есть на странице «Контейнеры».',
	'apps.installed.start': 'Запустить',
	'apps.installed.stop': 'Остановить',
	'apps.installed.restart': 'Перезапустить',
	'apps.installed.uninstall': 'Удалить',
	'apps.installed.confirmUninstall': {
		one: 'Удалить приложение {name}? Его контейнер будет удалён, а папки с данными останутся.',
		few: 'Удалить приложение {name}? Его {n} контейнера будут удалены, а папки с данными останутся.',
		many: 'Удалить приложение {name}? Его {n} контейнеров будут удалены, а папки с данными останутся.'
	},
	'apps.installed.loadError': 'Не удалось загрузить установленные приложения: {error}',
	'apps.installed.startFailed': 'Не удалось запустить приложение',
	'apps.installed.stopFailed': 'Не удалось остановить приложение',
	'apps.installed.restartFailed': 'Не удалось перезапустить приложение',
	'apps.installed.uninstallFailed': 'Не удалось удалить приложение',

	'apps.storage.unavailableTitle': 'Папки с данными недоступны',
	'apps.storage.unavailable':
		'Некоторые папки с данными приложения {name} находятся вне смонтированного пула хранения. Смонтируйте пул, в котором они лежат, на странице «Хранилище», затем запустите приложение.',

	'apps.detail.installed': 'Установлено',
	'apps.detail.website': 'Сайт',
	'apps.detail.github': 'GitHub',
	'apps.detail.docs': 'Документация',
	'apps.detail.loadingPools': 'Загрузка пулов хранения...',
	'apps.detail.poolsError': 'Не удалось загрузить пулы хранения',
	'apps.detail.noPoolTitle': 'Нет смонтированного пула хранения',
	'apps.detail.noPool':
		'Приложение {name} хранит данные в пуле хранения, а ни один пул не смонтирован. Создайте или смонтируйте пул на странице «Хранилище», затем установите приложение.',
	'apps.detail.goToStorage': 'Перейти в хранилище',
	'apps.detail.pool': 'Пул хранения',
	'apps.detail.poolHint': 'Данные приложения хранятся в этом пуле.',
	'apps.detail.poolOption': '{name} — свободно {free} из {total}',
	'apps.detail.unosSmb': 'Запускать в группе unos-smb',
	'apps.detail.unosSmbHint':
		'Задаёт PUID {puid}, PGID {pgid} (unos-smb) и UMASK 007. Только приложению из этой группы можно отдать общую папку на вкладке «Тома»: оно читает и изменяет в ней все файлы, а пользователи SMB могут изменять то, что оно записывает.',
	'apps.detail.unosSmbWarning':
		'Приложение {name} сможет читать и изменять файлы каждой общей папки, которую вы ему отдадите. Если оно доступно из интернета, тот, кто его захватит, получит и эти файлы.',
	'apps.detail.runsInUnosSmb':
		'Приложение {name} работает в группе unos-smb (PUID {puid}, PGID {pgid}): оно читает и изменяет файлы общих папок, которые ему отданы.',
	'apps.hardware.badge': 'Аппаратное ускорение',
	'apps.hardware.badgeMissing': 'Нет аппаратного ускорения',
	'apps.hardware.title': 'Аппаратное ускорение',
	'apps.hardware.generic': 'UNOS устанавливает {name} с оборудованием этого NAS, которое использует приложение.',
	'apps.hardware.profile.rk3588Video':
		'UNOS устанавливает {name} с видеооборудованием Rockchip RK3588 этого NAS, которое даёт его ядро: видеодекодерами и кодировщиками (MPP), RGA и GPU, в контейнере без привилегированного режима.',
	'apps.hardware.profile.rk3588Npu':
		'UNOS устанавливает машинное обучение {name} с NPU Rockchip RK3588 этого NAS: на нём работают умный поиск, распознавание лиц и распознавание текста, в контейнере без привилегированного режима.',
	'apps.hardware.missing':
		'Приложение {name} установлено без оборудования этого NAS, которое оно может использовать, поэтому аппаратное ускорение в нём не работает. Чтобы дать ему это оборудование, удалите {name} (папки с данными останутся) и установите его снова в тот же пул.',
	'apps.hardware.setting.jellyfin.rk3588Video':
		'В Jellyfin выберите Dashboard > Playback > Transcoding > Hardware acceleration: Rockchip MPP (RKMPP) (так пункты названы в английском интерфейсе Jellyfin) и отметьте кодеки для аппаратного декодирования.',
	'apps.hardware.setting.immich.rk3588Video':
		'Чтобы перекодировать видео аппаратно, выберите RKMPP в Immich в разделе Administration > Settings > Video Transcoding Settings > Hardware Acceleration (так пункты названы в английском интерфейсе Immich).',
	'apps.detail.tabs.overview': 'Обзор',
	'apps.detail.tabs.ports': 'Порты ({count})',
	'apps.detail.tabs.volumes': 'Тома ({count})',
	'apps.detail.tabs.environment': 'Окружение ({count})',
	'apps.detail.image': 'Образ Docker',
	'apps.detail.containers': 'Контейнеры',
	'apps.detail.containersHint':
		'Приложение {name} работает в {count} контейнерах в собственной сети: UNOS устанавливает, запускает, останавливает и удаляет их вместе. Каждый образ закреплён по дайджесту.',
	'apps.detail.diskAwake':
		'Пока {name} работает, жёсткие диски его пула не засыпают: его база данных и задания постоянно пишут на них. Чтобы они могли заснуть, остановите {name} на вкладке «Установленные».',
	'apps.detail.dataLocations': 'Расположение данных в пуле {pool}',
	'apps.detail.mountedAt': 'монтируется в {path}',
	'apps.detail.mountedAtReadOnly': 'монтируется в {path} (только чтение)',
	'apps.detail.mountedAtIn': 'монтируется в {path} в контейнере {container}',
	'apps.detail.mountedAtInReadOnly': 'монтируется в {path} в контейнере {container} (только чтение)',
	'apps.detail.generatedSecret': 'Пароль, который UNOS создаёт при установке и хранит в этом файле; он нигде не показывается.',
	'apps.detail.resources': 'Рекомендуемые ресурсы',
	'apps.detail.minMemory': 'Минимум памяти',
	'apps.detail.recommendedMemory': 'Рекомендуемая память',
	'apps.detail.ports': 'Порты',
	'apps.detail.volumes': 'Тома',
	'apps.detail.variables': 'Переменные',
	'apps.detail.portsHint':
		'Настройте сопоставление портов. Измените порты хоста, если они заняты другими службами.',
	'apps.detail.portsHostNetwork':
		'Приложение {name} работает в сети хоста и слушает эти порты на самом хосте, поэтому их нельзя изменить.',
	'apps.detail.hostPort': 'Порт хоста',
	'apps.detail.containerPort': 'Порт контейнера',
	'apps.detail.portRange': 'Укажите порт от 1 до 65535',
	'apps.detail.installedVolumesHint': 'Папки хоста, которые использует это установленное приложение.',
	'apps.detail.hostPath': 'Путь на хосте',
	'apps.detail.containerPath': 'Путь в контейнере',
	'apps.detail.readOnly': 'Только чтение',
	'apps.detail.readWrite': 'Чтение и запись',
	'apps.detail.notOnPool': 'Вне пула',
	'apps.detail.noVolumes': 'В это приложение не смонтированы папки хоста.',
	'apps.detail.volumesPoolHint':
		'Папки с данными создаются в выбранном пуле хранения. Папка с данными может указывать на другую папку внутри смонтированного пула, но не в снимок и не в корзину, а на общую папку — только у приложения, которое работает в группе unos-smb.',
	'apps.detail.volumesSystemHint': 'Системные пути с пометкой «Системный» монтируются с хоста как есть.',
	'apps.detail.insidePool': 'Путь должен быть внутри смонтированного пула хранения',
	'apps.detail.system': 'Системный',
	'apps.detail.file': 'Файл',
	'apps.detail.environmentHint':
		'Настройте переменные окружения. Обязательные переменные нужно задать до установки.',
	'apps.detail.secretPlaceholder': 'Придумайте уникальное значение',
	'apps.detail.optional': 'Необязательно',
	'apps.detail.enter': 'Введите {name}',
	'apps.detail.required': 'Обязательно',
	'apps.detail.setByUnos': 'Задаётся UNOS.',
	'apps.detail.setByUnosIn': 'Задаётся UNOS: приложение {name} работает в группе unos-smb.',
	'apps.detail.setByUnosOutside': 'Задаётся UNOS: приложение {name} работает вне группы unos-smb.',
	'apps.detail.setByUnosOption': 'Задаётся UNOS: {id} с параметром «Запускать в группе unos-smb», иначе {value}.',
	'apps.detail.setByUnosOptionGroup':
		'Задаётся UNOS: {id} (unos-smb) с параметром «Запускать в группе unos-smb», иначе {value}.',
	'apps.detail.missingVariable': 'Задайте обязательную переменную {names}.',
	'apps.detail.missingVariables': 'Задайте обязательные переменные {names}.',
	'apps.detail.invalidPorts': 'Порты хоста должны быть числами от 1 до 65535.',
	'apps.detail.invalidVolumes': 'Пути для {paths} должны быть внутри смонтированного пула хранения.',
	'apps.detail.installFailed': 'Не удалось установить приложение',
	'apps.detail.alreadyInstalled': 'Уже установлено',
	'apps.detail.install': 'Установить {name}',

	'apps.size.bytes': '{size} Б',
	'apps.size.kb': '{size} КБ',
	'apps.size.mb': '{size} МБ',
	'apps.size.gb': '{size} ГБ',
	'apps.size.tb': '{size} ТБ',

	'apps.detail.checking': 'Проверка пула, портов и свободного места...',
	'apps.detail.installRunning': 'Идёт установка приложения {name}: установите это приложение, когда она закончится.',
	'apps.job.label': 'Установка приложения {name}',
	'apps.job.state.running': 'Устанавливается',
	'apps.job.state.succeeded': 'Установлено',
	'apps.job.state.failed': 'Ошибка',
	'apps.job.step.checking': 'Проверка пула, портов и свободного места',
	'apps.job.step.pulling': 'Загрузка образов',
	'apps.job.step.creating': 'Создание папок и контейнеров',
	'apps.job.step.starting': 'Запуск контейнеров',
	'apps.job.images': 'Образы',
	'apps.job.containers': 'Контейнеры',
	'apps.job.image.waiting': 'Ожидает',
	'apps.job.image.present': 'Уже есть на NAS',
	'apps.job.image.pulling': 'Загружается',
	'apps.job.image.pulled': 'Загружен',
	'apps.job.image.failed': 'Ошибка',
	'apps.job.download': 'Загрузка образа {image}',
	'apps.job.bytes': '{done} из {total}',
	'apps.job.layers': { one: '{done} из {n} слоя', few: '{done} из {n} слоёв', many: '{done} из {n} слоёв' },
	'apps.job.container.waiting': 'Ожидает',
	'apps.job.container.created': 'Создан',
	'apps.job.container.starting': 'Запускается',
	'apps.job.container.running': 'Работает',
	'apps.job.container.failed': 'Ошибка',
	'apps.job.container.removed': 'Удалён',
	'apps.job.dismiss': 'Скрыть',
	'apps.job.state.cancelled': 'Отменено',
	'apps.job.step.cleaning': 'Удаление сделанного установкой',
	'apps.job.cancel': 'Отменить установку',
	'apps.job.cancelling': 'Отмена...',
	'apps.job.confirmCancel':
		'Отменить установку приложения {name}? Всё, что она успела сделать, будет удалено; папки с данными и загруженные образы останутся.',
	'apps.job.cancelledTitle': 'Установка отменена',
	'apps.detail.jobLost':
		'UNOS перезапустился во время установки, и её ход потерян. Что она успела сделать, видно на вкладке «Установленные».'
};
