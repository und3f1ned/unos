import {
	copyItems,
	createDirectory,
	downloadFile,
	listDirectory,
	moveItems,
	renameItem,
	uploadFile,
	type DirectoryListing,
	type FileInfo
} from '$lib/api/files';
import type { Settled } from '$lib/pages/read';
import { messageOf, signedOut } from './errors';
import { toggled } from './selection';

export type Transfer = 'copy' | 'move';

/** What failed, for the page to name in its language: the server's reason follows its words. */
export type FileFailure = { action: 'open' | 'create' | 'rename' | Transfer } | { action: 'download'; name: string };

/** The page's words, in its language: this module has no texts of its own (ADR-0287). */
export type FileBrowserTexts = {
	/** What failed; ": " and the server's reason follow. */
	failed: (failure: FileFailure) => string;
	/** The uploads that failed, `files` naming each with its reason. */
	uploadFailed: (files: string) => string;
};

/**
 * The state of the Files page: the folder that is open, the selection, the items chosen for Copy
 * or Move, the rename in a row, and what is on its way. The page creates one; its parts show it
 * and act through it.
 */
export class FileBrowser {
	listing = $state.raw<DirectoryListing | null>(null);
	loading = $state(true);
	// The pools directory, the top of the file manager. The server decides
	// where it is (UNOS_POOLS_BASE_PATH): it lists it when asked for no path.
	rootPath = $state<string | null>(null);
	// Directory shown; null until the first listing has arrived.
	currentPath = $state<string | null>(null);
	selected = $state.raw<Set<string>>(new Set());
	showNewFolder = $state(false);
	newFolderName = $state('');
	// The path of the item whose name is a field in its row, and the field's text.
	renaming = $state<string | null>(null);
	renamingValue = $state('');
	// A rename is on its way, until the list shows the new name: neither Enter
	// nor leaving the field sends another.
	renameSent = $state(false);
	error = $state('');
	// An upload, copy, move or delete is on its way.
	busy = $state(false);
	downloading = $state(false);
	// Items chosen with Copy or Move; they go to the folder that is open when
	// "Copy here" / "Move here" is chosen.
	pending = $state.raw<{ mode: Transfer; paths: string[] } | null>(null);

	// While an upload, copy, move, delete or rename is on its way, the toolbar's
	// actions and Copy here / Move here wait: they would start with paths that
	// are changing.
	working = $derived(this.busy || this.renameSent);
	selectedItems = $derived(this.listing ? this.listing.items.filter((item) => this.selected.has(item.path)) : []);
	// Rename works on one item at a time, Download on one regular file.
	selectedItem = $derived(this.selectedItems.length === 1 ? this.selectedItems[0] : null);
	selectedFile = $derived(this.selectedItem?.type === 'file' ? this.selectedItem : null);

	#texts: FileBrowserTexts;

	constructor(texts: FileBrowserTexts) {
		this.#texts = texts;
	}

	// The page's own loads so far (a folder opened, a reload after a change or of the open folder), and
	// their count when the last snapshot of its load was shown.
	#loads = 0;
	#appliedAt = 0;

	async load(path?: string) {
		this.#loads += 1;
		this.loading = true;
		try {
			this.#show(await listDirectory(path));
		} catch (e) {
			this.showError({ action: 'open' }, e);
		} finally {
			this.loading = false;
		}
	}

	#show(result: DirectoryListing) {
		this.listing = result;
		this.currentPath = result.path;
		if (result.parent === null) this.rootPath = result.path;
		// A name field goes with its item: in another folder, or once the item is gone.
		if (this.renaming !== null && !result.items.some((item) => item.path === this.renaming)) this.renaming = null;
	}

	/**
	 * The pools folder as the page's load read it ($lib/pages/files), as a load of it that has
	 * answered. Not after a load of the page's own started since the last one (another folder, a
	 * reload after a change): it read later. Another folder open than the pools folder stays open,
	 * and is read again (Refresh).
	 */
	apply(listing: Settled<DirectoryListing>) {
		if (this.#loads !== this.#appliedAt) return;
		if (this.currentPath !== null && this.currentPath !== this.rootPath) {
			void this.refresh();
			this.#appliedAt = this.#loads;
			return;
		}
		if (listing.ok) {
			this.#show(listing.value);
			// The failure of an earlier snapshot (the last visit's) is over.
			if (this.error === this.#applyError) this.error = '';
		} else {
			this.error = this.#applyError = `${this.#texts.failed({ action: 'open' })}: ${listing.error}`;
		}
		this.loading = false;
	}

	// The error that a snapshot of the page's load showed.
	#applyError = '';

	/** The next snapshot of the page's load (its Refresh) is newer than the loads of its own so far. */
	expectSnapshot() {
		this.#appliedAt = this.#loads;
	}

	refresh() {
		return this.load(this.currentPath ?? undefined);
	}

	async navigate(path: string) {
		this.selected = new Set();
		this.error = '';
		await this.load(path);
	}

	// A 401 has already ended the session and the sign-in page is on its way.
	showError(failure: FileFailure, e: unknown) {
		if (signedOut(e)) return;
		this.error = `${this.#texts.failed(failure)}: ${messageOf(e)}`;
	}

	/** A folder opens, a file downloads. */
	async open(item: FileInfo) {
		if (item.type === 'directory') {
			await this.navigate(item.path);
		} else if (item.type === 'file') {
			await this.download(item);
		}
	}

	async download(item: FileInfo) {
		this.error = '';
		this.downloading = true;
		try {
			await downloadFile(item.path);
		} catch (e) {
			this.showError({ action: 'download', name: item.name }, e);
		} finally {
			this.downloading = false;
		}
	}

	async createFolder() {
		if (!this.newFolderName.trim() || !this.currentPath) return;
		this.error = '';
		try {
			await createDirectory(this.currentPath, this.newFolderName.trim());
			this.newFolderName = '';
			this.showNewFolder = false;
			await this.refresh();
		} catch (e) {
			this.showError({ action: 'create' }, e);
		}
	}

	cancelNewFolder() {
		this.showNewFolder = false;
		this.newFolderName = '';
	}

	toggleSelect(path: string) {
		this.selected = toggled(this.selected, path);
	}

	startTransfer(mode: Transfer) {
		this.pending = { mode, paths: Array.from(this.selected) };
		this.selected = new Set();
	}

	async finishTransfer() {
		if (!this.pending || !this.currentPath) return;
		const { mode, paths } = this.pending;
		this.error = '';
		this.busy = true;
		try {
			if (mode === 'copy') {
				await copyItems(paths, this.currentPath);
			} else {
				await moveItems(paths, this.currentPath);
			}
			this.pending = null;
		} catch (e) {
			this.showError({ action: mode }, e);
		} finally {
			this.busy = false;
		}
		await this.refresh();
	}

	startRename(item: FileInfo) {
		this.renaming = item.path;
		this.renamingValue = item.name;
	}

	// Renames `item` to the name in its field: Enter and leaving the field call
	// it. Returns the item's path once its field is closed; null while the field
	// stays open because the server refused the name, and when there is nothing
	// to do: the blur of a field that is going away (after Escape or a rename),
	// or an Enter or a blur while the rename is on its way.
	async rename(item: FileInfo): Promise<string | null> {
		if (this.renaming !== item.path || this.renameSent) return null;
		const name = this.renamingValue.trim();
		if (!name || name === item.name || this.renamingValue === item.name) {
			this.renaming = null;
			return item.path;
		}
		this.error = '';
		// Until the list shows the new name, the toolbar's actions and Copy here /
		// Move here wait (`working`): they would start with the old path.
		this.renameSent = true;
		try {
			let renamed: FileInfo;
			try {
				renamed = await renameItem(item.path, name);
			} catch (e) {
				this.showError({ action: 'rename' }, e);
				return null;
			}
			this.renaming = null;
			// The selection, and the items chosen for Copy or Move, follow the item
			// (and what is in it) to its new name.
			const follow = (path: string) =>
				path === item.path || path.startsWith(`${item.path}/`) ? renamed.path + path.slice(item.path.length) : path;
			this.selected = new Set(Array.from(this.selected, follow));
			if (this.pending) this.pending = { ...this.pending, paths: this.pending.paths.map(follow) };
			await this.refresh();
			return renamed.path;
		} finally {
			this.renameSent = false;
		}
	}

	// Every file goes to the folder that is open now, also if another folder
	// is opened while the files are uploading.
	async upload(files: File[]) {
		const destination = this.currentPath;
		if (files.length === 0 || !destination) return;

		this.error = '';
		this.busy = true;
		const failed: string[] = [];
		try {
			for (const file of files) {
				try {
					await uploadFile(destination, file);
				} catch (err) {
					if (signedOut(err)) return;
					failed.push(`${file.name} (${messageOf(err)})`);
				}
			}
		} finally {
			this.busy = false;
		}
		if (failed.length > 0) this.error = this.#texts.uploadFailed(failed.join(', '));
		await this.refresh();
	}
}
