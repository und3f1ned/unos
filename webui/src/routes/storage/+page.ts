import { readPage } from '$lib/pages/load';
import { storageReader } from '$lib/pages/storage';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, storageReader);
