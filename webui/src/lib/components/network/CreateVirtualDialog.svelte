<script lang="ts">
	// The dialog that makes a bond, a VLAN or a bridge (ADR-0424), with the fields of `form`: the
	// ports or the parent it may take, its addressing, and the warning when the change touches the
	// interface of the connection to UNOS or of the default route, which then waits to be kept.
	import type { HostInterface, VirtualInterface, VirtualKind } from '$lib/api/network';
	import { Alert, Button, Input, Modal, Select } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import type { VirtualForm } from './virtualForm.svelte';

	type Props = {
		form: VirtualForm;
		// The host's interfaces, and the bonds, VLANs and bridges that UNOS made.
		hosts: HostInterface[];
		items: VirtualInterface[];
		// The interfaces that carry the connection to UNOS or the default route.
		protectedNames: Set<string>;
		revertSeconds: number;
		busy: boolean;
		oncancel: () => void;
		onsubmit: () => void;
	};

	let { form, hosts, items, protectedNames, revertSeconds, busy, oncancel, onsubmit }: Props = $props();

	const TITLES: Record<VirtualKind, string> = {
		bond: 'virtual.dialog.bond',
		vlan: 'virtual.dialog.vlan',
		bridge: 'virtual.dialog.bridge'
	};

	let kindOptions = $derived([
		{ value: 'bond', label: $t('virtual.kind.bond') },
		{ value: 'vlan', label: $t('virtual.kind.vlan') },
		{ value: 'bridge', label: $t('virtual.kind.bridge') }
	]);
	let modeOptions = $derived([
		{ value: '802.3ad', label: $t('virtual.mode.lacp') },
		{ value: 'balance-rr', label: $t('virtual.mode.roundRobin') }
	]);
	let addressingOptions = $derived([
		{ value: 'none', label: $t('virtual.addressing.none') },
		{ value: 'dhcp', label: $t('virtual.addressing.dhcp') },
		{ value: 'static', label: $t('virtual.addressing.static') }
	]);

	function carriesVlans(iface: HostInterface): boolean {
		return hosts.some((other) => other.parent === iface.name) || items.some((item) => item.parent === iface.name);
	}
	// A port loses its own addresses: only the one that carries the connection, whose settings the
	// bond or bridge takes over, may have any (the server refuses the others).
	function free(iface: HostInterface): boolean {
		return !iface.wireless && !iface.master && !carriesVlans(iface) && (iface.protected || iface.addresses.length === 0);
	}
	let bondPorts = $derived(hosts.filter((iface) => iface.kind === 'physical' && free(iface)));
	let bridgePorts = $derived(hosts.filter((iface) => ['physical', 'bond', 'vlan'].includes(iface.kind) && free(iface)));
	let vlanParents = $derived(hosts.filter((iface) => ['physical', 'bond'].includes(iface.kind) && !iface.wireless && !iface.master));
	let candidates = $derived(form.creating === 'bond' ? bondPorts : bridgePorts);
	let parentOptions = $derived(vlanParents.map((iface) => ({ value: iface.name, label: iface.name })));
	// The interfaces of the change in the dialog that carry the connection to UNOS or the default route.
	let touched = $derived(
		form.creating === 'vlan'
			? form.parent && protectedNames.has(form.parent)
				? [form.parent]
				: []
			: form.ports.filter((port) => protectedNames.has(port))
	);
	let vlanDefault = $derived(form.parent && form.vlanId ? `${form.parent}.${form.vlanId}` : '');
</script>

<Modal
	open={form.creating !== null}
	title={form.creating ? $t(TITLES[form.creating]) : ''}
	size="md"
	closable={!busy}
	onclose={oncancel}
>
	{#if form.creating}
		<form
			class="space-y-4"
			onsubmit={(e) => {
				e.preventDefault();
				onsubmit();
			}}
		>
			<Select
				label={$t('virtual.kindLabel')}
				options={kindOptions}
				value={form.creating}
				onchange={(kind) => form.open(kind as VirtualKind)}
			/>
			{#if form.creating === 'vlan'}
				<Select label={$t('virtual.parent')} options={parentOptions} bind:value={form.parent} />
				<Input label={$t('virtual.vlanId')} type="number" hint={$t('virtual.vlanIdHint')} bind:value={form.vlanId} />
				<Input
					label={$t('virtual.name')}
					hint={vlanDefault ? $t('virtual.vlanNameHint', { name: vlanDefault }) : $t('virtual.nameHint')}
					maxlength={15}
					bind:value={form.name}
				/>
			{:else}
				<Input label={$t('virtual.name')} hint={$t('virtual.nameHint')} maxlength={15} required bind:value={form.name} />
				{#if form.creating === 'bond'}
					<Select label={$t('virtual.mode')} hint={$t('virtual.modeHint')} options={modeOptions} bind:value={form.mode} />
				{/if}
				<fieldset>
					<legend class="form-label">{$t('virtual.ports')}</legend>
					<p class="text-xs text-content-muted mb-2">
						{form.creating === 'bond' ? $t('virtual.bondPortsHint') : $t('virtual.bridgePortsHint')}
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
										checked={form.ports.includes(iface.name)}
										onchange={(e) => form.togglePort(iface.name, e.currentTarget.checked)}
									/>
									<span class="break-all">{iface.name}</span>
								</label>
							{/each}
						</div>
					{/if}
				</fieldset>
			{/if}
			<Input label={$t('virtual.mtu')} type="number" hint={$t('virtual.mtuHint')} bind:value={form.mtu} />
			{#if form.creating !== 'vlan' && touched.length}
				<p class="text-sm text-content-secondary">
					{$t('virtual.takesOver', { name: form.name || form.creating, port: touched[0] })}
				</p>
			{:else}
				<Select label={$t('virtual.addressing')} options={addressingOptions} bind:value={form.addressing} />
				{#if form.addressing === 'static'}
					<Input label={$t('virtual.addresses')} hint={$t('virtual.addressesHint')} bind:value={form.addressesText} />
				{:else if form.addressing === 'dhcp'}
					<p class="text-xs text-content-muted">{$t('virtual.dhcpHint')}</p>
				{/if}
			{/if}
			{#if touched.length}
				<Alert variant="warning">{$t('virtual.waitsWarning', { names: touched.join(', '), n: revertSeconds })}</Alert>
			{:else}
				<p class="text-xs text-content-muted">{$t('virtual.noRevertNote')}</p>
			{/if}
			{#if form.error}
				<Alert variant="error"><p class="break-words">{form.error}</p></Alert>
			{/if}
		</form>
	{/if}
	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={busy} onclick={oncancel}>
			{$t('common.cancel')}
		</Button>
		<Button variant="primary" class="flex-1" loading={busy} onclick={onsubmit}>{$t('virtual.create')}</Button>
	{/snippet}
</Modal>
