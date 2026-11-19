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
		type VirtualKind,
		type VirtualStatus,
		type WaitingChange
	} from '$lib/api/network';
	import { Alert, Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { GitMerge, Plus } from 'lucide-svelte';
	import CreateVirtualDialog from './CreateVirtualDialog.svelte';
	import DeleteVirtualDialog from './DeleteVirtualDialog.svelte';
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
	let success = $state('');
	let notice = $state('');

	// The dialog of a new interface, and its fields.
	const form = new VirtualForm();
	let deleting = $state<VirtualInterface | null>(null);

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
	// The buttons that open the dialog, one for each kind.
	const CREATE: { kind: VirtualKind; key: string }[] = [
		{ kind: 'bond', key: 'virtual.createBond' },
		{ kind: 'vlan', key: 'virtual.createVlan' },
		{ kind: 'bridge', key: 'virtual.createBridge' }
	];

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
		success = $t(done, { name: result.name });
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
		success = '';
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

	async function confirmDelete() {
		const item = deleting;
		if (!item) return;
		busy = true;
		error = '';
		success = '';
		notice = '';
		try {
			const result = await deleteVirtualInterface(item.name);
			deleting = null;
			busy = false;
			await afterChange(result, 'virtual.deleted');
		} catch (e) {
			deleting = null;
			error = messageOf(e) || $t('virtual.deleteFailed');
			await load();
		} finally {
			busy = false;
		}
	}

	async function keep() {
		busy = true;
		error = '';
		success = '';
		notice = '';
		try {
			await keepVirtualChange();
			stopCountdown();
			success = $t('virtual.kept');
		} catch (e) {
			error = messageOf(e) || $t('virtual.keepFailed');
		} finally {
			busy = false;
		}
		await load();
	}

	function changeText(change: WaitingChange | null): string {
		return change
			? $t(CHANGES[change.action] ?? 'virtual.change.create', { kind: labelOf($t, KIND_WORDS, change.kind), name: change.name })
			: $t('virtual.change.unknown');
	}
</script>

<section class="card" aria-labelledby="virtual-title">
	<div class="flex flex-wrap items-center justify-between gap-2 mb-3">
		<h3 id="virtual-title" class="card-title">
			<GitMerge class="w-4 h-4 text-content-secondary" />
			{$t('virtual.title')}
		</h3>
		{#if status?.supported}
			<div class="flex flex-wrap gap-2">
				{#each CREATE as button (button.kind)}
					<Button size="sm" variant="secondary" icon={Plus} disabled={busy || waits} onclick={() => form.open(button.kind)}>
						{$t(button.key)}
					</Button>
				{/each}
			</div>
		{/if}
	</div>

	<p class="text-sm text-content-secondary mb-3">{$t('virtual.description')}</p>

	{#if success}
		<Alert variant="success" class="mb-3">{success}</Alert>
	{/if}
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
	{#if loadError}
		<Alert variant="error" title={$t('virtual.loadFailed')} class="mb-3">
			<p class="break-words">{loadError}</p>
			<Button variant="secondary" size="sm" class="mt-3" {loading} onclick={load}>{$t('common.retry')}</Button>
		</Alert>
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
			<p class="text-sm text-content-muted">{$t('virtual.none')}</p>
		{:else}
			<VirtualInterfaceList items={status.interfaces} disabled={busy || waits} ondelete={(item) => (deleting = item)} />
		{/if}
	{/if}
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

<DeleteVirtualDialog
	{deleting}
	{protectedNames}
	{revertSeconds}
	{busy}
	oncancel={() => (deleting = null)}
	onconfirm={confirmDelete}
/>
