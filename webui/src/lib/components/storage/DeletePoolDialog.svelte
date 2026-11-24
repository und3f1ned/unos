<script lang="ts">
	// The dialog that removes a pool, in one step: what it does, as a short list of
	// consequences, then the pool's name typed into an empty field. The data stays on the disks.
	import { Alert, ConfirmDialog } from '$lib/components/ui';
	import { ApiError } from '$lib/api/client';
	import { deletePool, type Pool } from '$lib/api/storage';
	import { moveCriticalApps } from '$lib/api/standby';
	import { t } from '$lib/i18n';
	import CriticalAppsOffer from './CriticalAppsOffer.svelte';
	import { criticalAppsOf } from './criticalApps';
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
	// The critical apps of the pool that a refused removal offers to move to the SD standby first (ADR-0498).
	let critical = $state<string[]>([]);

	function closeDeleteModal() {
		if (deleting) return;
		deleteError = '';
		critical = [];
		onclose();
	}

	async function moveAndRetry() {
		if (!pool) return;
		deleting = true;
		try {
			await moveCriticalApps('planned', pool.name);
			critical = [];
		} catch (e) {
			deleteError = messageOf(e);
			deleting = false;
			return;
		}
		deleting = false;
		await handleDeletePool();
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
			critical = e instanceof ApiError && e.status === 409 ? await criticalAppsOf(pool.name) : [];
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
		{#if critical.length > 0}
			<CriticalAppsOffer apps={critical} busy={deleting} onmove={moveAndRetry} />
		{/if}
	{/if}
</ConfirmDialog>
