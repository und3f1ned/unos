<script module lang="ts">
	/** What a restore did: the server's message and the items it restored, or why it failed. */
	export type RestoreOutcome = { success: boolean; message: string; restored?: string[] };
</script>

<script lang="ts">
	// The dialog of a restore from a backup: the user types the backup's name, and its passphrase
	// for an encrypted one (.tar.gz.enc), then sees what the restore did. The page shows it while
	// it is open, so that its fields start empty every time.
	import type { BackupInfo } from '$lib/api/backup';
	import { Alert, Button, Input, Modal } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { AlertTriangle, CheckCircle, RotateCcw } from 'lucide-svelte';

	type Props = {
		target: BackupInfo;
		// The end of the restore; null until it ends.
		result: RestoreOutcome | null;
		// The backup being restored.
		restoring: string | null;
		onclose: () => void;
		onrestore: (passphrase: string) => void;
	};

	let { target, result, restoring, onclose, onrestore }: Props = $props();

	let confirmName = $state('');
	// Encrypted backups (.tar.gz.enc) are restored with their passphrase.
	let passphrase = $state('');

	// The items of a restore that the server names ("restored" of POST /backup/restore): keys of
	// the catalog, by item.
	const RESTORED_ITEMS: Record<string, string> = {
		database: 'backup.dialog.items.database',
		smb_config: 'backup.dialog.items.smbConfig'
	};

	/** The label of a restored item in the language of the page; an item without one as it comes. */
	function itemLabel(item: string): string {
		return Object.prototype.hasOwnProperty.call(RESTORED_ITEMS, item) ? $t(RESTORED_ITEMS[item]) : item;
	}
</script>

<!-- A Modal (ADR-0432) whose name stays the same while its view changes from the question to the
     result. -->
<Modal
	open
	title={$t('backup.dialog.title')}
	size="sm"
	tone={result ? null : 'warning'}
	closable={restoring === null}
	{onclose}
>
	{#if result}
		<!-- Result view -->
		<div class="text-center">
			{#if result.success}
				<CheckCircle class="w-12 h-12 text-status-success mx-auto mb-4" aria-hidden="true" />
				<h3 class="text-lg font-semibold mb-2">{$t('backup.dialog.complete')}</h3>
				<p class="text-content-secondary text-sm">{result.message}</p>
				{#if result.restored && result.restored.length > 0}
					<div class="card-nested p-3 mt-4 text-left">
						<p class="text-xs text-content-secondary mb-2">{$t('backup.dialog.restored')}</p>
						<ul class="text-sm space-y-1">
							{#each result.restored as item}
								<li class="list-inside list-disc">{itemLabel(item)}</li>
							{/each}
						</ul>
					</div>
				{/if}
			{:else}
				<AlertTriangle class="w-12 h-12 text-status-error mx-auto mb-4" aria-hidden="true" />
				<h3 class="text-lg font-semibold mb-2">{$t('backup.dialog.failed')}</h3>
				<p class="text-status-error text-sm">{result.message}</p>
			{/if}
		</div>
	{:else}
		<!-- Confirmation view -->
		<p class="text-content-secondary text-sm mb-4">
			{$t('backup.dialog.from')} <span class="text-content font-medium break-all">{target.filename}</span>
		</p>

		<!-- What the restore replaces, as a warning: the consequences, as a list. -->
		<Alert variant="warning" size="sm" class="mb-4" title={$t('backup.dialog.warning')}>
			<ul class="mt-1 list-disc space-y-1 pl-5">
				<li>{$t('backup.dialog.database')}</li>
				<li>{$t('backup.dialog.shares')}</li>
			</ul>
		</Alert>

		{#if target.encrypted}
			<Input
				class="mb-4"
				id="passphrase"
				type="password"
				label={$t('backup.dialog.passphrase')}
				autocomplete="off"
				required
				bind:value={passphrase}
			/>
		{/if}

		<!-- The name to type stands above the field, which starts empty: a placeholder would look
         like a value already typed. A name to type, not one click to copy: no select-all. -->
		<div>
			<label for="confirmName" class="form-label">
				{$t('backup.dialog.confirm')}
			</label>
			<p class="mb-2 font-mono text-xs break-all text-content" data-testid="restore-expected">{target.filename}</p>
			<input
				type="text"
				id="confirmName"
				bind:value={confirmName}
				autocomplete="off"
				spellcheck="false"
				class="form-input text-sm"
			/>
		</div>
	{/if}

	{#snippet footer()}
		{#if result}
			<Button variant="secondary" onclick={onclose}>{$t('backup.dialog.close')}</Button>
		{:else}
			<Button variant="secondary" class="flex-1" disabled={restoring !== null} onclick={onclose}>
				{$t('common.cancel')}
			</Button>
			<Button
				variant="danger"
				class="flex-1"
				icon={RotateCcw}
				loading={restoring !== null}
				onclick={() => onrestore(passphrase)}
				disabled={confirmName !== target.filename || (target.encrypted && !passphrase)}
			>
				{$t('backup.restore')}
			</Button>
		{/if}
	{/snippet}
</Modal>
