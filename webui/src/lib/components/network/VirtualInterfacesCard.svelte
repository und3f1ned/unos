<script lang="ts">
	import { onDestroy, onMount, tick } from 'svelte';
	import {
		createBond,
		createBridge,
		createVlan,
		deleteVirtualInterface,
		getVirtualInterfaces,
		keepVirtualChange,
		type VirtualChange,
		type VirtualInterface,
		type VirtualStatus,
		type WaitingChange
	} from '$lib/api/network';
	import { Alert, Button, EmptyState, LoadState, Skeleton, confirm, toast } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { Network, Plus } from 'lucide-svelte';
	import CreateVirtualDialog from './CreateVirtualDialog.svelte';
	import { labelOf, messageOf } from './network';
	import VirtualInterfaceList from './VirtualInterfaceList.svelte';
	import { VirtualForm } from './virtualForm.svelte';

	// System > Network, administrators only: the bonds, VLANs and bridges that UNOS makes on the host
	// with systemd-networkd (ADR-0424). A change that touches the interface of the default route or
	// of the connection to UNOS waits to be kept: the card counts down and offers Keep, and the host
	// undoes the change by itself when the time runs out, also when this page cannot reach UNOS.

	type Props = {
		// A bond, VLAN or bridge was made or deleted, or a change was undone: the host's interfaces
		// changed.
		onchanged?: () => void;
	};

	let { onchanged }: Props = $props();

	// Seconds the host's revert may take after its time before the card reads the state again, and
	// how often it reads again while the host still undoes the change.
	const REVERT_GRACE_MS = 5000;
	const REVERT_CHECKS = 3;

	let status = $state<VirtualStatus | null>(null);
	let loading = $state(false);
	let loadError = $state('');
	let busy = $state(false);
	let error = $state('');
	let notice = $state('');

	// The dialog of a new interface, and its fields.
	const form = new VirtualForm();

	// The countdown of a change that waits to be kept, in the page's clock.
	let deadline = $state<number | null>(null);
	let now = $state(Date.now());
	let ticker: ReturnType<typeof setInterval> | null = null;
	let check: ReturnType<typeof setTimeout> | null = null;
	let checksLeft = 0;
	let keepButton = $state<HTMLButtonElement | null>(null);

	let revertSeconds = $derived(status?.revert_seconds ?? 120);
	let secondsLeft = $derived(deadline === null ? null : Math.max(0, Math.ceil((deadline - now) / 1000)));
	let waits = $derived(secondsLeft !== null || (status?.revert_in ?? null) !== null || !!status?.reverting);

	const KIND_WORDS: Record<string, string> = {
		bond: 'virtual.kindWord.bond',
		vlan: 'virtual.kindWord.vlan',
		bridge: 'virtual.kindWord.bridge'
	};
	const CHANGES: Record<string, string> = { create: 'virtual.change.create', delete: 'virtual.change.delete' };

	let hosts = $derived(status?.host_interfaces ?? []);
	let protectedNames = $derived(new Set(hosts.filter((iface) => iface.protected).map((iface) => iface.name)));

	onMount(() => {
		void load();
	});

	onDestroy(() => {
		if (ticker) clearInterval(ticker);
		if (check) clearTimeout(check);
	});

	async function load() {
		loading = true;
		try {
			status = await getVirtualInterfaces();
			loadError = '';
			follow(status.revert_in);
		} catch (e) {
			loadError = messageOf(e);
		} finally {
			loading = false;
		}
	}

	// A change that the host still counts down: a running countdown keeps its own deadline, and one
	// found after a reload starts one.
	function follow(revertIn: number | null) {
		if (revertIn === null) {
			if (!check) deadline = null;
		} else if (deadline === null) {
			deadline = Date.now() + revertIn * 1000;
		}
		syncTicker();
	}

	function syncTicker() {
		now = Date.now();
		if (deadline !== null && !ticker) {
			// polling-ignore: a countdown; when the time to keep a change has run out, the state is read once to
			// see what the host's revert left, and at most twice more while the host still undoes it (ADR-0365).
			ticker = setInterval(onTick, 1000);
		} else if (deadline === null && ticker) {
			clearInterval(ticker);
			ticker = null;
		}
	}

	function onTick() {
		now = Date.now();
		if (deadline !== null && deadline <= now && !check) {
			checksLeft = REVERT_CHECKS;
			check = setTimeout(checkRevert, REVERT_GRACE_MS);
		}
	}

	async function checkRevert() {
		check = null;
		deadline = null;
		syncTicker();
		checksLeft -= 1;
		try {
			status = await getVirtualInterfaces();
		} catch (e) {
			// The connection may come back with the host's own settings a moment later.
			if (checksLeft > 0) {
				notice = $t('virtual.reverting');
				check = setTimeout(checkRevert, REVERT_GRACE_MS);
			} else error = messageOf(e);
			return;
		}
		if (status.reverting && checksLeft > 0) {
			notice = $t('virtual.reverting');
			check = setTimeout(checkRevert, REVERT_GRACE_MS);
		} else if (status.revert_failed) error = $t('virtual.revertFailed');
		else if (status.revert_in === null && !status.reverting) notice = $t('virtual.undone');
		else if (status.revert_in !== null && status.revert_in > 0) startCountdown(status.revert_in);
		else error = $t('virtual.notYet');
		onchanged?.();
	}

	function startCountdown(revertIn: number) {
		notice = '';
		deadline = Date.now() + revertIn * 1000;
		syncTicker();
	}

	function stopCountdown() {
		deadline = null;
		if (check) clearTimeout(check);
		check = null;
		syncTicker();
	}

	async function focusKeep() {
		if (deadline === null) return;
		await tick();
		keepButton?.focus();
	}

	async function afterChange(result: VirtualChange, done: string) {
		toast.success($t(done, { name: result.name }));
		if (result.revert_in !== null) startCountdown(result.revert_in);
		await load();
		onchanged?.();
		await focusKeep();
	}

	async function submitCreate() {
		const kind = form.creating;
		if (!kind) return;
		const mtuValue = form.parsedMtu();
		if (mtuValue === undefined) {
			form.error = $t('virtual.badMtu');
			return;
		}
		if (kind === 'bond' && form.ports.length < 2) {
			form.error = $t('virtual.needPorts');
			return;
		}
		const id = Number(form.vlanId);
		if (kind === 'vlan' && !form.parent) {
			form.error = $t('virtual.needParent');
			return;
		}
		if (kind === 'vlan' && !(Number.isInteger(id) && id >= 1 && id <= 4094)) {
			form.error = $t('virtual.badId');
			return;
		}
		const fields = {
			mtu: mtuValue,
			addressing: form.addressing,
			addresses: form.addressing === 'static' ? form.addresses() : []
		};
		busy = true;
		form.error = '';
		error = '';
		notice = '';
		try {
			let result: VirtualChange;
			const name = form.name.trim();
			if (kind === 'bond') result = await createBond({ name, mode: form.mode, ports: form.ports, ...fields });
			else if (kind === 'bridge') result = await createBridge({ name, ports: form.ports, ...fields });
			else result = await createVlan({ parent: form.parent, vlan_id: id, name: name || null, ...fields });
			form.creating = null;
			busy = false;
			await afterChange(result, 'virtual.created');
		} catch (e) {
			form.error = messageOf(e) || $t('virtual.createFailed');
			// A change that failed may wait for the host to undo it.
			await load();
			onchanged?.();
		} finally {
			busy = false;
		}
	}

	/**
	 * Delete asks first, and warns when the deletion touches the interface of the connection to UNOS
	 * or of the default route: it then waits to be kept (ADR-0424).
	 */
	async function confirmDelete(item: VirtualInterface) {
		const touches = [item.name, ...item.ports, ...(item.parent ? [item.parent] : [])].filter((n) =>
			protectedNames.has(n)
		);
		const yes = await confirm({
			title: $t('virtual.deleteTitle', { name: item.name }),
			message: $t('virtual.deleteText', { name: item.name }),
			warning: touches.length ? $t('virtual.waitsWarning', { names: touches.join(', '), n: revertSeconds }) : undefined,
			confirmLabel: $t('virtual.delete', { name: item.name })
		});
		if (!yes) return;
		busy = true;
		error = '';
		notice = '';
		try {
			const result = await deleteVirtualInterface(item.name);
			busy = false;
			await afterChange(result, 'virtual.deleted');
		} catch (e) {
			error = messageOf(e) || $t('virtual.deleteFailed');
			await load();
		} finally {
			busy = false;
		}
	}

	async function keep() {
		busy = true;
		error = '';
		notice = '';
		try {
			await keepVirtualChange();
			stopCountdown();
			toast.success($t('virtual.kept'));
		} catch (e) {
			error = messageOf(e) || $t('virtual.keepFailed');
		} finally {
			busy = false;
		}
		await load();
	}

	function changeText(change: WaitingChange | null): string {
		return change
			? $t(CHANGES[change.action] ?? 'virtual.change.create', {
					kind: labelOf($t, KIND_WORDS, change.kind),
					name: change.name
				})
			: $t('virtual.change.unknown');
	}
</script>

<section class="card" aria-labelledby="virtual-title">
	<!-- One Create, whose dialog asks for the kind, in place of a button per kind. -->
	<div class="card-header">
		<div class="min-w-0">
			<h3 id="virtual-title" class="card-title">{$t('virtual.title')}</h3>
			<p class="card-description">{$t('virtual.description')}</p>
		</div>
		{#if status?.supported}
			<Button size="sm" variant="secondary" icon={Plus} disabled={busy || waits} onclick={() => form.open('bond')}>
				{$t('virtual.createAny')}
			</Button>
		{/if}
	</div>

	{#if notice}
		<Alert variant="info" class="mb-3">{notice}</Alert>
	{/if}
	{#if error}
		<Alert variant="error" class="mb-3"><p class="break-words">{error}</p></Alert>
	{/if}
	{#if status?.reverting && secondsLeft === null && notice !== $t('virtual.reverting')}
		<Alert variant="info" class="mb-3">{$t('virtual.reverting')}</Alert>
	{/if}
	{#if status?.revert_failed && error !== $t('virtual.revertFailed')}
		<Alert variant="error" class="mb-3">{$t('virtual.revertFailed')}</Alert>
	{/if}
	{#if secondsLeft !== null}
		<Alert variant="warning" class="mb-3">
			<p id="virtual-countdown">
				{$t('virtual.waiting', { n: secondsLeft, change: changeText(status?.change ?? null) })}
			</p>
			<div class="mt-3">
				<Button
					variant="primary"
					size="sm"
					disabled={busy}
					aria-describedby="virtual-countdown"
					bind:el={keepButton}
					onclick={keep}>{$t('virtual.keep')}</Button
				>
			</div>
		</Alert>
	{/if}

	<!-- The first load's rows (ADR-0430); a failed load in their place, or above an earlier one's. -->
	<LoadState
		loading={status === null && !loadError}
		error={loadError}
		errorTitle={$t('virtual.loadFailed')}
		keepOnError={status !== null}
		onretry={load}
		retrying={loading}
	>
		{#snippet skeleton()}<Skeleton shape="row" count={2} />{/snippet}
		{#if status && !status.supported}
			<Alert variant="info">
				{$t('virtual.unsupported', {
					manager: status.manager === 'none' ? $t('virtual.managerNone') : status.manager
				})}
			</Alert>
		{:else if status}
			{#if status.unreadable.length}
				<Alert variant="warning" class="mb-3">{$t('virtual.unreadable', { n: status.unreadable.length })}</Alert>
			{/if}
			{#if status.interfaces.length === 0}
				<EmptyState compact icon={Network} title={$t('virtual.none')} description={$t('virtual.noneHint')} />
			{:else}
				<VirtualInterfaceList items={status.interfaces} disabled={busy || waits} ondelete={confirmDelete} />
			{/if}
		{/if}
	</LoadState>
</section>

<CreateVirtualDialog
	{form}
	{hosts}
	items={status?.interfaces ?? []}
	{protectedNames}
	{revertSeconds}
	{busy}
	oncancel={() => (form.creating = null)}
	onsubmit={submitCreate}
/>
