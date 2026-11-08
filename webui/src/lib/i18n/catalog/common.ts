import type { Translation } from '../translate';

// Words that many areas use. Areas add their own keys to their own file (ADR-0287).

export const en = {
	'common.product': 'UNOS',
	'common.loading': 'Loading...',
	'common.retry': 'Retry',
	'common.cancel': 'Cancel',
	'common.refresh': 'Refresh',
	'common.done': 'Done',
	'common.unknown': 'Unknown',
	'common.username': 'Username',
	'common.password': 'Password'
};

export const ru: Translation<typeof en> = {
	'common.product': 'UNOS',
	'common.loading': 'Загрузка...',
	'common.retry': 'Повторить',
	'common.cancel': 'Отмена',
	'common.refresh': 'Обновить',
	'common.done': 'Готово',
	'common.unknown': 'Неизвестно',
	'common.username': 'Имя пользователя',
	'common.password': 'Пароль'
};
