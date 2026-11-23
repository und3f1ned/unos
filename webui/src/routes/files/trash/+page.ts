import { readPage } from '$lib/pages/load';
import { trashReader } from '$lib/pages/files';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, trashReader);
