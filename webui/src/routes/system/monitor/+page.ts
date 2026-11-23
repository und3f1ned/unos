import { readPage } from '$lib/pages/load';
import { monitorReader } from '$lib/pages/system/monitor';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, monitorReader);
