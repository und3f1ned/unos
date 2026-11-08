import type { Translation } from '../translate';

// The texts of the shared components of src/lib/components/ui (ADR-0012, ADR-0197).

export const en = {
	'ui.alert.dismiss': 'Dismiss',
	'ui.modal.close': 'Close modal',
	'ui.emptyState.title': 'No data',
	'ui.select.placeholder': 'Select an option'
};

export const ru: Translation<typeof en> = {
	'ui.alert.dismiss': 'Закрыть',
	'ui.modal.close': 'Закрыть окно',
	'ui.emptyState.title': 'Нет данных',
	'ui.select.placeholder': 'Выберите значение'
};
