<script lang="ts">
	// The dialog that removes a pool, in one step: what it does, as a short list of
	// consequences, then the pool's name typed into an empty field. The data stays on the disks.
	// The ConfirmDialog of the design system takes it over when it comes.
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

	let deleteConfirmName = $state('');
	let deleting = $state(false);
	let deleteError = $state('');

	function reset() {
		deleteConfirmName = '';
		deleteError = '';
	}

	function closeDeleteModal() {
		if (deleting) return;
		reset();
		onclose();
	}

	async function handleDeletePool() {
		// Enter in the field submits too: one request at a time.
		if (!pool || deleting || deleteConfirmName !== pool.name) return;
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
		<form class="space-y-4" onsubmit={(e) => { e.preventDefault(); handleDeletePool(); }}>
			<p>
				{$t('storage.remove.about')}
				<strong class="text-content">{quoted(pool.name)}</strong>{$t('storage.remove.aboutEnd')}
			</p>
			<ul class="list-disc ml-5 space-y-1 text-sm text-secondary">
				<li>
					{$t('storage.remove.unmounted')}
					<span class="mono-xs break-all">{pool.mounted ? pool.mountpoint || '-' : $t('storage.pool.notMounted')}</span>
				</li>
				<li>
					{$t('storage.remove.kept', {
						used: pool.mounted ? formatBytes(pool.used_size) : $t('storage.pool.unknown')
					})}
					<span class="mono-xs break-all">{pool.devices.join(', ') || '-'}</span>
				</li>
				<li>{$t('storage.remove.users')}</li>
			</ul>
			<!-- The name to type is in the label; the field starts empty. -->
			<Input
				label={$t('storage.remove.confirm', { name: pool.name })}
				bind:value={deleteConfirmName}
				autocomplete="off"
			/>
			{#if deleteError}
				<Alert variant="error" size="sm" title={$t('storage.remove.failed')}>{deleteError}</Alert>
			{/if}
		</form>
	{/if}

	{#snippet footer()}
		{#if pool}
			<Button variant="secondary" class="flex-1" disabled={deleting} onclick={closeDeleteModal}>
				{$t('common.cancel')}
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
	{/snippet}
</Modal>
