import { readPage } from '$lib/pages/load';
import { filesReader } from '$lib/pages/files';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, filesReader);
