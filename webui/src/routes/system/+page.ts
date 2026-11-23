import { readPage } from '$lib/pages/load';
import { generalReader } from '$lib/pages/system/general';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, generalReader);
