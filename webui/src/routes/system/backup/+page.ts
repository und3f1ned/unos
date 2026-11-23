import { readPage } from '$lib/pages/load';
import { backupReader } from '$lib/pages/system/backup';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, backupReader);
