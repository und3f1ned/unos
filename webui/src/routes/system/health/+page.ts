import { readPage } from '$lib/pages/load';
import { healthReader } from '$lib/pages/system/health';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, healthReader);
