<script lang="ts">
	// The dialog that mounts or unmounts a pool; a Mount also imports a pool found on the disks
	// (ADR-0283).
	import { Alert, ConfirmDialog } from '$lib/components/ui';
	import { ApiError } from '$lib/api/client';
	import { mountPool, unmountPool } from '$lib/api/storage';
	import { moveCriticalApps } from '$lib/api/standby';
	import { t } from '$lib/i18n';
	import CriticalAppsOffer from './CriticalAppsOffer.svelte';
	import { criticalAppsOf } from './criticalApps';
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
	// The critical apps of the pool that a refused unmount offers to move to the SD standby first (ADR-0498).
	let critical = $state<string[]>([]);

	function close() {
		if (busy) return;
		error = '';
		critical = [];
		onclose();
	}

	/** The pool's critical apps, read after the refusal without holding the dialog (ADR-0498). */
	async function offer(name: string) {
		const apps = await criticalAppsOf(name);
		if (action?.pool.name === name && error) critical = apps;
	}

	async function moveAndRetry() {
		if (!action) return;
		busy = true;
		try {
			await moveCriticalApps('planned', action.pool.name);
			critical = [];
		} catch (e) {
			error = messageOf(e);
			busy = false;
			return;
		}
		busy = false;
		await confirm();
	}

	async function confirm() {
		if (!action) return;
		const { kind, pool } = action;
		busy = true;
		error = '';
		critical = [];
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
			if (kind === 'unmount' && e instanceof ApiError && e.status === 409) void offer(pool.name);
		} finally {
			busy = false;
		}
	}
</script>

<ConfirmDialog
	open={action !== null}
	title={$t(action?.kind === 'mount' ? 'storage.mount.title' : 'storage.unmount.title')}
	confirmLabel={$t(action?.kind === 'mount' ? 'storage.mount.confirm' : 'storage.unmount.confirm')}
	tone={action?.kind === 'unmount' ? 'warning' : 'neutral'}
	{busy}
	oncancel={close}
	onconfirm={confirm}
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
			<Alert variant="error" title={$t(action.kind === 'mount' ? 'storage.mount.failed' : 'storage.unmount.failed')}>
				{error}
			</Alert>
		{/if}
		{#if critical.length > 0}
			<CriticalAppsOffer apps={critical} {busy} onmove={moveAndRetry} />
		{/if}
	{/if}
</ConfirmDialog>
