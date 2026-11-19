<script lang="ts">
	// The dialog that mounts or unmounts a pool; a Mount also imports a pool found on the disks
	// (ADR-0283).
	import { Alert, Button, Modal } from '$lib/components/ui';
	import { mountPool, unmountPool } from '$lib/api/storage';
	import { t } from '$lib/i18n';
	import { messageOf, poolMountpoint, type PoolAction } from './pools';

	type Props = {
		/** The dialog is open while it is set. */
		action: PoolAction | null;
		/** A label or a name shown on its own, in the quotes of the page's language. */
		quoted: (name: string) => string;
		onclose: () => void;
		/** The pool was mounted or unmounted. */
		ondone: () => void | Promise<void>;
	};

	let { action, quoted, onclose, ondone }: Props = $props();

	let busy = $state(false);
	let error = $state('');

	function close() {
		if (busy) return;
		error = '';
		onclose();
	}

	async function confirm() {
		if (!action) return;
		const { kind, pool } = action;
		busy = true;
		error = '';
		try {
			if (kind === 'mount') {
				await mountPool(pool.name);
			} else {
				await unmountPool(pool.name);
			}
			busy = false;
			error = '';
			await ondone();
		} catch (e) {
			// e.g. 409 "target is busy" when unmounting
			error = messageOf(e);
		} finally {
			busy = false;
		}
	}
</script>

<Modal
	open={action !== null}
	title={$t(action?.kind === 'mount' ? 'storage.mount.title' : 'storage.unmount.title')}
	size="md"
	closable={!busy}
	onclose={close}
>
	{#if action}
		{#if action.kind === 'mount'}
			<p>
				{$t('storage.mount.question')}
				<strong class="text-content">{quoted(action.pool.name)}</strong>
				{$t('storage.mount.at')}
				<span class="mono">{poolMountpoint(action.pool.name)}</span>?
			</p>
			<!-- Also how a pool found on the disks is imported (ADR-0283). -->
			<p class="text-sm text-secondary">{$t('storage.mount.kept')}</p>
		{:else}
			<p>
				{$t('storage.unmount.question')}
				<strong class="text-content">{quoted(action.pool.name)}</strong>?
			</p>
			<Alert variant="warning">
				{$t('storage.unmount.before')}
				<span class="mono-xs">{action.pool.mountpoint || poolMountpoint(action.pool.name)}</span>
				{$t('storage.unmount.after')}
			</Alert>
		{/if}
		{#if error}
			<Alert
				variant="error"
				title={$t(action.kind === 'mount' ? 'storage.mount.failed' : 'storage.unmount.failed')}
			>
				{error}
			</Alert>
		{/if}
	{/if}

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={busy} onclick={close}>
			{$t('common.cancel')}
		</Button>
		<Button
			variant={action?.kind === 'unmount' ? 'warning' : 'primary'}
			class="flex-1"
			loading={busy}
			onclick={confirm}
		>
			{$t(action?.kind === 'mount' ? 'storage.pool.mount' : 'storage.pool.unmount')}
		</Button>
	{/snippet}
</Modal>
