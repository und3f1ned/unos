import { readPage } from '$lib/pages/load';
import { sharesReader } from '$lib/pages/shares';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, sharesReader);
