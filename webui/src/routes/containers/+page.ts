import { readPage } from '$lib/pages/load';
import { containersReader } from '$lib/pages/containers';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, containersReader);
