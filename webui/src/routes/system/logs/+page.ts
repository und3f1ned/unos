import { readPage } from '$lib/pages/load';
import { logsReader } from '$lib/pages/system/logs';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, logsReader);
