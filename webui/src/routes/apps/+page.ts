import { readPage } from '$lib/pages/load';
import { appsReader } from '$lib/pages/apps';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, appsReader);
