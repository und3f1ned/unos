<script lang="ts">
	import { createEventDispatcher, onDestroy, onMount, tick } from 'svelte';
	import {
		createBond,
		createBridge,
		createVlan,
		deleteVirtualInterface,
		getVirtualInterfaces,
		keepVirtualChange,
		type Addressing,
		type HostInterface,
		type VirtualChange,
		type VirtualInterface,
		type VirtualKind,
		type VirtualStatus,
		type WaitingChange
	} from '$lib/api/network';
	import { Alert, Badge, Button, Input, Modal, Select } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { GitMerge, Plus, Trash2 } from 'lucide-svelte';

	// System > Network, administrators only: the bonds, VLANs and bridges that UNOS makes on the host
	// with systemd-networkd (ADR-0424). A change that touches the interface of the default route or
	// of the connection to UNOS waits to be kept: the card counts down and offers Keep, and the host
	// undoes the change by itself when the time runs out, also when this page cannot reach UNOS.

	const dispatch = createEventDispatcher<{ changed: void }>();

	// Seconds the host's revert may take after its time before the card reads the state again, and
	// how often it reads again while the host still undoes the change.
	const REVERT_GRACE_MS = 5000;
	const REVERT_CHECKS = 3;

	let status: VirtualStatus | null = null;
	let loading = false;
	let loadError = '';
	let busy = false;
	let error = '';
	let success = '';
	let notice = '';

	// The dialog of a new interface, and its fields.
	let creating: VirtualKind | null = null;
	let formError = '';
	let name = '';
	let mode: '802.3ad' | 'balance-rr' = '802.3ad';
	let ports: string[] = [];
	let parent = '';
	let vlanId = '';
	let mtu = '';
	let addressing: Addressing = 'none';
	let addressesText = '';
	let deleting: VirtualInterface | null = null;

	// The countdown of a change that waits to be kept, in the page's clock.
	let deadline: number | null = null;
	let now = Date.now();
	let ticker: ReturnType<typeof setInterval> | null = null;
	let check: ReturnType<typeof setTimeout> | null = null;
	let checksLeft = 0;
	let keepButton: HTMLButtonElement | null = null;

	$: revertSeconds = status?.revert_seconds ?? 120;
	$: secondsLeft = deadline === null ? null : Math.max(0, Math.ceil((deadline - now) / 1000));
	$: waits = secondsLeft !== null || (status?.revert_in ?? null) !== null || !!status?.reverting;

	const KINDS: Record<string, string> = {
		bond: 'virtual.kind.bond',
		vlan: 'virtual.kind.vlan',
		bridge: 'virtual.kind.bridge'
	};
	const KIND_WORDS: Record<string, string> = {
		bond: 'virtual.kindWord.bond',
		vlan: 'virtual.kindWord.vlan',
		bridge: 'virtual.kindWord.bridge'
	};
	const CHANGES: Record<string, string> = { create: 'virtual.change.create', delete: 'virtual.change.delete' };
	const MODES: Record<string, string> = { '802.3ad': 'virtual.mode.lacp', 'balance-rr': 'virtual.mode.roundRobin' };
	const ADDRESSING: Record<string, string> = {
		none: 'virtual.addressing.none',
		dhcp: 'virtual.addressing.dhcp',
		static: 'virtual.addressing.static'
	};
	const TITLES: Record<VirtualKind, string> = {
		bond: 'virtual.dialog.bond',
		vlan: 'virtual.dialog.vlan',
		bridge: 'virtual.dialog.bridge'
	};

	$: modeOptions = [
		{ value: '802.3ad', label: $t('virtual.mode.lacp') },
		{ value: 'balance-rr', label: $t('virtual.mode.roundRobin') }
	];
	$: addressingOptions = [
		{ value: 'none', label: $t('virtual.addressing.none') },
		{ value: 'dhcp', label: $t('virtual.addressing.dhcp') },
		{ value: 'static', label: $t('virtual.addressing.static') }
	];

	$: labelOf = (labels: Record<string, string>, value: string | null): string =>
		value !== null && Object.prototype.hasOwnProperty.call(labels, value) ? $t(labels[value]) : value ?? '';

	$: hosts = status?.host_interfaces ?? [];
	$: carriesVlans = (iface: HostInterface): boolean =>
		hosts.some((other) => other.parent === iface.name) ||
		(status?.interfaces ?? []).some((item) => item.parent === iface.name);
	// A port loses its own addresses: only the one that carries the connection, whose settings the
	// bond or bridge takes over, may have any (the server refuses the others).
	$: free = (iface: HostInterface): boolean =>
		!iface.wireless && !iface.master && !carriesVlans(iface) && (iface.protected || iface.addresses.length === 0);
	$: bondPorts = hosts.filter((iface) => iface.kind === 'physical' && free(iface));
	$: bridgePorts = hosts.filter((iface) => ['physical', 'bond', 'vlan'].includes(iface.kind) && free(iface));
	$: vlanParents = hosts.filter((iface) => ['physical', 'bond'].includes(iface.kind) && !iface.wireless && !iface.master);
	$: candidates = creating === 'bond' ? bondPorts : bridgePorts;
	$: parentOptions = vlanParents.map((iface) => ({ value: iface.name, label: iface.name }));
	$: protectedNames = new Set(hosts.filter((iface) => iface.protected).map((iface) => iface.name));
	// The interfaces of the change in the dialog that carry the connection to UNOS or the default route.
	$: touched =
		creating === 'vlan'
			? parent && protectedNames.has(parent)
				? [parent]
				: []
			: ports.filter((port) => protectedNames.has(port));
	$: vlanDefault = parent && vlanId ? `${parent}.${vlanId}` : '';
	$: deletionTouches = deleting
		? [deleting.name, ...deleting.ports, ...(deleting.parent ? [deleting.parent] : [])].filter((n) =>
				protectedNames.has(n)
			)
		: [];

	onMount(() => {
		void load();
	});

	onDestroy(() => {
		if (ticker) clearInterval(ticker);
		if (check) clearTimeout(check);
	});

	function messageOf(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

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
		dispatch('changed');
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

	function openCreate(kind: VirtualKind) {
		creating = kind;
		formError = '';
		name = kind === 'bond' ? 'bond0' : kind === 'bridge' ? 'br0' : '';
		mode = '802.3ad';
		ports = [];
		parent = '';
		vlanId = '';
		mtu = '';
		addressing = 'none';
		addressesText = '';
	}

	function togglePort(port: string, checked: boolean) {
		ports = checked ? [...ports.filter((p) => p !== port), port] : ports.filter((p) => p !== port);
	}

	function parsedMtu(): number | null | undefined {
		if (!mtu.trim()) return null;
		const value = Number(mtu);
		return Number.isInteger(value) && value >= 1280 && value <= 9216 ? value : undefined;
	}

	async function afterChange(result: VirtualChange, done: string) {
		success = $t(done, { name: result.name });
		if (result.revert_in !== null) startCountdown(result.revert_in);
		await load();
		dispatch('changed');
		await focusKeep();
	}

	async function submitCreate() {
		const kind = creating;
		if (!kind) return;
		const mtuValue = parsedMtu();
		if (mtuValue === undefined) {
			formError = $t('virtual.badMtu');
			return;
		}
		if (kind === 'bond' && ports.length < 2) {
			formError = $t('virtual.needPorts');
			return;
		}
		const id = Number(vlanId);
		if (kind === 'vlan' && !parent) {
			formError = $t('virtual.needParent');
			return;
		}
		if (kind === 'vlan' && !(Number.isInteger(id) && id >= 1 && id <= 4094)) {
			formError = $t('virtual.badId');
			return;
		}
		const fields = {
			mtu: mtuValue,
			addressing,
			addresses:
				addressing === 'static'
					? addressesText
							.split(',')
							.map((a) => a.trim())
							.filter((a) => a)
					: []
		};
		busy = true;
		formError = '';
		error = '';
		success = '';
		notice = '';
		try {
			let result: VirtualChange;
			if (kind === 'bond') result = await createBond({ name: name.trim(), mode, ports, ...fields });
			else if (kind === 'bridge') result = await createBridge({ name: name.trim(), ports, ...fields });
			else result = await createVlan({ parent, vlan_id: id, name: name.trim() || null, ...fields });
			creating = null;
			busy = false;
			await afterChange(result, 'virtual.created');
		} catch (e) {
			formError = messageOf(e) || $t('virtual.createFailed');
			// A change that failed may wait for the host to undo it.
			await load();
			dispatch('changed');
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

	$: changeText = (change: WaitingChange | null): string =>
		change
			? $t(CHANGES[change.action] ?? 'virtual.change.create', { kind: labelOf(KIND_WORDS, change.kind), name: change.name })
			: $t('virtual.change.unknown');
	$: describe = (item: VirtualInterface): string => {
		if (item.kind === 'bond')
			return $t('virtual.bondOf', { mode: labelOf(MODES, item.mode), ports: item.ports.join(', ') });
		if (item.kind === 'vlan') return $t('virtual.vlanOn', { id: String(item.vlan_id), parent: item.parent ?? '' });
		return $t('virtual.bridgeOf', { ports: item.ports.length ? item.ports.join(', ') : $t('virtual.noPorts') });
	};
	$: addressingLine = (item: VirtualInterface): string => {
		if (item.addressing === 'copied') return $t('virtual.copiedFrom', { name: item.copied_from ?? '' });
		if (item.addressing === 'static') return item.addresses.join(', ');
		return labelOf(ADDRESSING, item.addressing);
	};
</script>

<section class="card" aria-labelledby="virtual-title">
	<div class="flex flex-wrap items-center justify-between gap-2 mb-3">
		<h3 id="virtual-title" class="card-title">
			<GitMerge class="w-4 h-4 text-content-secondary" />
			{$t('virtual.title')}
		</h3>
		{#if status?.supported}
			<div class="flex flex-wrap gap-2">
				<Button size="sm" variant="secondary" icon={Plus} disabled={busy || waits} on:click={() => openCreate('bond')}>
					{$t('virtual.createBond')}
				</Button>
				<Button size="sm" variant="secondary" icon={Plus} disabled={busy || waits} on:click={() => openCreate('vlan')}>
					{$t('virtual.createVlan')}
				</Button>
				<Button size="sm" variant="secondary" icon={Plus} disabled={busy || waits} on:click={() => openCreate('bridge')}>
					{$t('virtual.createBridge')}
				</Button>
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
			<Button variant="secondary" size="sm" class="mt-3" {loading} on:click={load}>{$t('common.retry')}</Button>
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
					on:click={keep}>{$t('virtual.keep')}</Button
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
			<ul class="space-y-2" aria-label={$t('virtual.list')}>
				{#each status.interfaces as item (item.name)}
					<li class="flex flex-wrap items-center justify-between gap-2 p-3 bg-surface-overlay rounded-lg">
						<div class="min-w-0">
							<div class="flex flex-wrap items-center gap-2">
								<span class="font-semibold text-content break-all">{item.name}</span>
								<Badge variant="info">{labelOf(KINDS, item.kind)}</Badge>
								{#if item.present}
									<Badge variant={item.state === 'UP' ? 'success' : 'warning'}>{item.state}</Badge>
								{:else}
									<Badge variant="neutral">{$t('virtual.absent')}</Badge>
								{/if}
							</div>
							<p class="text-sm text-content-secondary break-words">{describe(item)}</p>
							<p class="text-xs text-content-muted break-words">
								<span>{addressingLine(item)}</span>
								{#if item.mtu}<span class="ml-2">{$t('virtual.mtuOf', { mtu: String(item.mtu) })}</span>{/if}
							</p>
						</div>
						<Button
							variant="danger"
							size="sm"
							icon={Trash2}
							disabled={busy || waits}
							aria-label={$t('virtual.deleteLabel', { name: item.name })}
							on:click={() => (deleting = item)}>{$t('virtual.delete')}</Button
						>
					</li>
				{/each}
			</ul>
		{/if}
	{/if}
</section>

<Modal
	open={creating !== null}
	title={creating ? $t(TITLES[creating]) : ''}
	size="md"
	closable={!busy}
	on:close={() => (creating = null)}
>
	{#if creating}
		<form class="space-y-4" on:submit|preventDefault={submitCreate}>
			{#if creating === 'vlan'}
				<Select label={$t('virtual.parent')} options={parentOptions} bind:value={parent} />
				<Input label={$t('virtual.vlanId')} type="number" hint={$t('virtual.vlanIdHint')} bind:value={vlanId} />
				<Input
					label={$t('virtual.name')}
					hint={vlanDefault ? $t('virtual.vlanNameHint', { name: vlanDefault }) : $t('virtual.nameHint')}
					maxlength={15}
					bind:value={name}
				/>
			{:else}
				<Input label={$t('virtual.name')} hint={$t('virtual.nameHint')} maxlength={15} required bind:value={name} />
				{#if creating === 'bond'}
					<Select label={$t('virtual.mode')} hint={$t('virtual.modeHint')} options={modeOptions} bind:value={mode} />
				{/if}
				<fieldset>
					<legend class="form-label">{$t('virtual.ports')}</legend>
					<p class="text-xs text-content-muted mb-2">
						{creating === 'bond' ? $t('virtual.bondPortsHint') : $t('virtual.bridgePortsHint')}
					</p>
					{#if candidates.length === 0}
						<p class="text-sm text-content-muted">{$t('virtual.noCandidates')}</p>
					{:else}
						<div class="flex flex-wrap gap-x-4 gap-y-2">
							{#each candidates as iface (iface.name)}
								<label class="flex items-center gap-2 text-sm text-content">
									<input
										type="checkbox"
										class="form-checkbox"
										checked={ports.includes(iface.name)}
										on:change={(e) => togglePort(iface.name, e.currentTarget.checked)}
									/>
									<span class="break-all">{iface.name}</span>
								</label>
							{/each}
						</div>
					{/if}
				</fieldset>
			{/if}
			<Input label={$t('virtual.mtu')} type="number" hint={$t('virtual.mtuHint')} bind:value={mtu} />
			{#if creating !== 'vlan' && touched.length}
				<p class="text-sm text-content-secondary">
					{$t('virtual.takesOver', { name: name || creating, port: touched[0] })}
				</p>
			{:else}
				<Select label={$t('virtual.addressing')} options={addressingOptions} bind:value={addressing} />
				{#if addressing === 'static'}
					<Input label={$t('virtual.addresses')} hint={$t('virtual.addressesHint')} bind:value={addressesText} />
				{:else if addressing === 'dhcp'}
					<p class="text-xs text-content-muted">{$t('virtual.dhcpHint')}</p>
				{/if}
			{/if}
			{#if touched.length}
				<Alert variant="warning">{$t('virtual.waitsWarning', { names: touched.join(', '), n: revertSeconds })}</Alert>
			{:else}
				<p class="text-xs text-content-muted">{$t('virtual.noRevertNote')}</p>
			{/if}
			{#if formError}
				<Alert variant="error"><p class="break-words">{formError}</p></Alert>
			{/if}
		</form>
	{/if}
	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={busy} on:click={() => (creating = null)}>
			{$t('common.cancel')}
		</Button>
		<Button variant="primary" class="flex-1" loading={busy} on:click={submitCreate}>{$t('virtual.create')}</Button>
	</svelte:fragment>
</Modal>

<Modal
	open={deleting !== null}
	title={deleting ? $t('virtual.deleteTitle', { name: deleting.name }) : ''}
	size="md"
	danger
	closable={!busy}
	on:close={() => (deleting = null)}
>
	{#if deleting}
		<p>{$t('virtual.deleteText', { name: deleting.name })}</p>
		{#if deletionTouches.length}
			<Alert variant="warning" class="mt-3">
				{$t('virtual.waitsWarning', { names: deletionTouches.join(', '), n: revertSeconds })}
			</Alert>
		{/if}
	{/if}
	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={busy} on:click={() => (deleting = null)}>
			{$t('common.cancel')}
		</Button>
		<Button variant="danger" class="flex-1" loading={busy} on:click={confirmDelete}>{$t('virtual.delete')}</Button>
	</svelte:fragment>
</Modal>
