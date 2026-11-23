import { dashboardReader } from '$lib/pages/dashboard';
import { readPage } from '$lib/pages/load';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, dashboardReader);
