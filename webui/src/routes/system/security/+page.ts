import { readPage } from '$lib/pages/load';
import { securityReader } from '$lib/pages/system/security';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, securityReader);
