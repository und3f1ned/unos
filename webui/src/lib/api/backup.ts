import { api, apiFetch, downloadAs } from './client';

export interface BackupInfo {
	filename: string;
	size: number;
	created: string;
	version: string;
	/** Encrypted with a passphrase (.tar.gz.enc); restoring it needs the passphrase. */
	encrypted: boolean;
}

/** The body of POST /backup/create: every backup holds the same files, so there are no options of what it holds. */
export interface BackupConfig {
	encrypt: boolean;
	/** Required when encrypt is true. */
	passphrase: string;
}

export interface RestoreResult {
	status: string;
	/** What was applied: "database", "smb_config". */
	restored: string[];
	/** manifest.json of the backup, as stored in the archive. */
	manifest: {
		version?: string;
		created?: string;
		hostname?: string;
		[key: string]: unknown;
	};
	/** Summary for the user; it includes the warnings. */
	message: string;
	/** What was skipped or not restored automatically (e.g. scheduled_tasks.json, an invalid share). */
	warnings: string[];
	/** Copy of the database taken before it was replaced, if it was. */
	previous_database: string | null;
}

export async function listBackups(): Promise<{ backups: BackupInfo[] }> {
	return api.get<{ backups: BackupInfo[] }>('/backup/list');
}

export async function createBackup(config?: Partial<BackupConfig>): Promise<{ status: string; backup: BackupInfo }> {
	return api.post<{ status: string; backup: BackupInfo }>('/backup/create', config || {});
}

export async function downloadBackup(filename: string): Promise<void> {
	await downloadAs(`/backup/download/${encodeURIComponent(filename)}`, filename);
}

/**
 * Upload a backup file: .tar.gz, or .tar.gz.enc for an encrypted one (its
 * content is checked when it is restored with the passphrase; a .tar.gz.enc
 * that is gzip data is refused). The server stores it under a new name,
 * uploaded_<date>_<time>[_<n>] with the same extension.
 */
export async function uploadBackup(file: File): Promise<{ status: string; backup: BackupInfo }> {
	const formData = new FormData();
	formData.append('file', file);
	const response = await apiFetch('/backup/upload', { method: 'POST', body: formData });
	return response.json();
}

/**
 * Restore a backup. The body is {"passphrase": ...}: required for encrypted
 * backups, ignored for the others (an empty object is sent for them).
 */
export async function restoreBackup(filename: string, passphrase?: string): Promise<RestoreResult> {
	return api.post<RestoreResult>(`/backup/restore/${encodeURIComponent(filename)}`, passphrase ? { passphrase } : {});
}

export async function deleteBackup(filename: string): Promise<{ status: string }> {
	return api.delete<{ status: string }>(`/backup/${encodeURIComponent(filename)}`);
}
