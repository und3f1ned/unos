import { readPage } from '$lib/pages/load';
import { updatesReader } from '$lib/pages/system/updates';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, updatesReader);
