// The reads of Files (routes/files/+page.ts) and of its trash (routes/files/trash/+page.ts).
//
// Files keeps the folder that is open in the page, not in its URL: its load reads the pools folder,
// where the page opens (the server lists it when asked for no path), and the hidden files that
// interrupted uploads left, at once; the page reads every other folder itself. Its key is the
// page's alone: no folder of the URL is part of it.
import {
	listDirectory,
	listTrash,
	listUploadLeftovers,
	type DirectoryListing,
	type TrashListing,
	type UploadLeftover
} from '$lib/api/files';
import type { PageReader } from './load';
import { settle, type Settled } from './read';

export type FilesSnapshot = {
	/** The pools folder. */
	listing: Settled<DirectoryListing>;
	leftovers: Settled<UploadLeftover[]>;
};

async function readFiles(): Promise<FilesSnapshot> {
	const [listing, leftovers] = await Promise.all([
		settle(listDirectory()),
		settle(listUploadLeftovers().then((answer) => (Array.isArray(answer?.files) ? answer.files : [])))
	]);
	return { listing, leftovers };
}

export const filesReader: PageReader<FilesSnapshot> = {
	key: 'files',
	// The pools folder lists the mounted pools (storage), and restores put copies into them (snapshots).
	areas: ['files', 'storage', 'snapshots'],
	adminOnly: true,
	read: readFiles
};

export type TrashSnapshot = { trash: Settled<TrashListing> };

export const trashReader: PageReader<TrashSnapshot> = {
	key: 'trash',
	// The trash of each mounted pool and volume (storage).
	areas: ['files', 'storage'],
	adminOnly: true,
	read: async () => ({ trash: await settle(listTrash()) })
};
