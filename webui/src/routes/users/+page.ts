import { readPage } from '$lib/pages/load';
import { usersReader } from '$lib/pages/users';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, usersReader);
