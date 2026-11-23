import { accountReader } from '$lib/pages/account';
import { readPage } from '$lib/pages/load';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, accountReader);
