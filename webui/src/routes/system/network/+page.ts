import { readPage } from '$lib/pages/load';
import { networkReader } from '$lib/pages/system/network';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, networkReader);
