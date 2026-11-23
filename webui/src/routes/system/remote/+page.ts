import { readPage } from '$lib/pages/load';
import { remoteReader } from '$lib/pages/system/remote';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, remoteReader);
