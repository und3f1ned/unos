import { readPage } from '$lib/pages/load';
import { notificationsReader } from '$lib/pages/system/notifications';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, notificationsReader);
