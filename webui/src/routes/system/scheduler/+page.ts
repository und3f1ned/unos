import { readPage } from '$lib/pages/load';
import { schedulerReader } from '$lib/pages/system/scheduler';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, schedulerReader);
