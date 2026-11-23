import { readPage } from '$lib/pages/load';
import { powerReader } from '$lib/pages/system/power';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, powerReader);
