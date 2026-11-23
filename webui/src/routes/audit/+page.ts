import { auditReader } from '$lib/pages/audit';
import { readPage } from '$lib/pages/load';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, auditReader);
