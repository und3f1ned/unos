import { readPage } from '$lib/pages/load';
import { snapshotsReader } from '$lib/pages/snapshots';
import type { PageLoad } from './$types';

export const load: PageLoad = (event) => readPage(event, snapshotsReader);
