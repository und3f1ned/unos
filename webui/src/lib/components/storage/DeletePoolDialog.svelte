<script lang="ts">
	// The dialog that removes a pool, in one step: what it does, as a short list of
	// consequences, then the pool's name typed into an empty field. The data stays on the disks.
	import { Alert, ConfirmDialog } from '$lib/components/ui';
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

	let deleting = $state(false);
	let deleteError = $state('');

	function closeDeleteModal() {
		if (deleting) return;
		deleteError = '';
		onclose();
	}

	// The dialog confirms only once the name typed matches the pool's, exactly.
	async function handleDeletePool() {
		if (!pool || deleting) return;
		deleting = true;
		deleteError = '';
		try {
			await deletePool(pool.name);
			deleting = false;
			await ondeleted();
		} catch (e) {
			deleteError = messageOf(e);
		} finally {
			deleting = false;
		}
	}
</script>

<ConfirmDialog
	open={pool !== null}
	title={$t('storage.remove.title')}
	confirmLabel={$t('storage.remove.submit')}
	typeToConfirm={pool?.name ?? ''}
	typeLabel={$t('storage.remove.confirm', { name: pool?.name ?? '' })}
	typeExact
	busy={deleting}
	oncancel={closeDeleteModal}
	onconfirm={handleDeletePool}
>
	{#if pool}
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
		{#if deleteError}
			<Alert variant="error" size="sm" title={$t('storage.remove.failed')}>{deleteError}</Alert>
		{/if}
	{/if}
</ConfirmDialog>
