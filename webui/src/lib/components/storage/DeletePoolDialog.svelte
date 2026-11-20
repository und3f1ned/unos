<script lang="ts">
	// The dialog that removes a pool in two steps: what it holds, then its name typed again. The
	// data stays on the disks.
	import { AlertTriangle } from 'lucide-svelte';
	import { Alert, Button, Input, Modal } from '$lib/components/ui';
	import { deletePool, type Pool } from '$lib/api/storage';
	import { t } from '$lib/i18n';
	import { messageOf } from './pools';

	type Props = {
		/** The pool to remove; the dialog is open while it is set. */
		pool: Pool | null;
		formatBytes: (bytes: number) => string;
		/** A label or a name shown on its own, in the quotes of the page's language. */
		quoted: (name: string) => string;
		onclose: () => void;
		/** The pool was removed. */
		ondeleted: () => void | Promise<void>;
	};

	let { pool, formatBytes, quoted, onclose, ondeleted }: Props = $props();

	let deleteStep: 1 | 2 = $state(1);
	let deleteConfirmName = $state('');
	let deleting = $state(false);
	let deleteError = $state('');

	function reset() {
		deleteStep = 1;
		deleteConfirmName = '';
		deleteError = '';
	}

	function closeDeleteModal() {
		if (deleting) return;
		reset();
		onclose();
	}

	function proceedToStep2() {
		deleteStep = 2;
	}

	async function handleDeletePool() {
		if (!pool || deleteConfirmName !== pool.name) return;
		deleting = true;
		deleteError = '';
		try {
			await deletePool(pool.name);
			deleting = false;
			reset();
			await ondeleted();
		} catch (e) {
			deleteError = messageOf(e);
		} finally {
			deleting = false;
		}
	}
</script>

<Modal
	open={pool !== null}
	title={$t('storage.remove.title')}
	size="md"
	danger
	closable={!deleting}
	onclose={closeDeleteModal}
>
	{#snippet titleIcon()}
		<AlertTriangle class="w-6 h-6" />
	{/snippet}

	{#if pool}
		{#if deleteStep === 1}
			<div class="space-y-4">
				<Alert variant="warning" title={$t('storage.remove.warningTitle')}>
					<p>
						{$t('storage.remove.about')}
						<strong class="text-content">{quoted(pool.name)}</strong>.
					</p>
					<p class="mt-2 text-sm">{$t('storage.remove.kept')}</p>
				</Alert>

				<div class="card-nested text-sm">
					<p class="text-secondary">{$t('storage.remove.details')}</p>
					<ul class="mt-2 space-y-1">
						<li>{$t('storage.remove.size', { size: formatBytes(pool.total_size) })}</li>
						<li>
							{$t('storage.remove.used', {
								used: pool.mounted ? formatBytes(pool.used_size) : $t('storage.pool.unknown')
							})}
						</li>
						<li>{$t('storage.remove.devices', { devices: pool.devices.join(', ') || '-' })}</li>
						<li>
							{$t('storage.remove.mount', {
								mount: pool.mounted ? pool.mountpoint || '-' : $t('storage.pool.notMounted')
							})}
						</li>
					</ul>
				</div>
			</div>
		{:else}
			<div class="space-y-4">
				<Alert variant="error" title={$t('storage.remove.finalTitle')}>
					<p>{$t('storage.remove.typeName')}</p>
					<p class="font-mono text-lg mt-2 bg-surface-overlay px-3 py-2 rounded-lg">
						{pool.name}
					</p>
				</Alert>

				<Input
					label={$t('storage.remove.confirm')}
					bind:value={deleteConfirmName}
					placeholder={$t('storage.remove.confirmPlaceholder')}
					autocomplete="off"
				/>
			</div>
		{/if}
		{#if deleteError}
			<Alert variant="error" title={$t('storage.remove.failed')}>{deleteError}</Alert>
		{/if}
	{/if}

	{#snippet footer()}
		{#if pool}
			{#if deleteStep === 1}
				<Button variant="secondary" class="flex-1" onclick={closeDeleteModal}
					>{$t('common.cancel')}</Button
				>
				<Button variant="danger" class="flex-1" onclick={proceedToStep2}
					>{$t('storage.remove.continue')}</Button
				>
			{:else}
				<Button
					variant="secondary"
					class="flex-1"
					disabled={deleting}
					onclick={() => (deleteStep = 1)}
				>
					{$t('storage.remove.back')}
				</Button>
				<Button
					variant="danger"
					class="flex-1"
					loading={deleting}
					disabled={deleteConfirmName !== pool.name}
					onclick={handleDeletePool}
				>
					{$t('storage.remove.submit')}
				</Button>
			{/if}
		{/if}
	{/snippet}
</Modal>
