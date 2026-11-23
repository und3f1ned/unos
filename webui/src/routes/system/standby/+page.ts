import { readPage } from '$lib/pages/load';
import { standbyReader } from '$lib/pages/system/standby';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, standbyReader);
