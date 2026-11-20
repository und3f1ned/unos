<script lang="ts">
	import { messageOf } from '$lib/errors';
	import { onMount, untrack } from 'svelte';
	import {
		addAppName,
		applyAppNames,
		getAppNames,
		removeAppName,
		type AppName,
		type AppNamesStatus
	} from '$lib/api/remoteAccess';
	import { Alert, Button, Card, Input, Select } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { Plus, Trash2 } from 'lucide-svelte';
	import ConfirmDialog from './ConfirmDialog.svelte';
	import FormDialog from './FormDialog.svelte';

	// System > Network, administrators only: the local names of apps (ADR-0420, ADR-0431), routed by the
	// web UI's Caddy to a published port of the app and announced by avahi on the LAN.

	let status = $state<AppNamesStatus | null>(null);
	let loading = $state(false);
	let loadError = $state('');
	let busy = $state(false);
	let error = $state('');
	let success = $state('');
	let showAdd = $state(false);
	// The chosen published port: its index in status.apps, each app's web port first.
	let choice = $state('');
	let label = $state('');
	let addError = $state('');
	let removing = $state<AppName | null>(null);

	let host = $derived(status?.host_name ?? '');
	let appOptions = $derived(
		(status?.apps ?? []).map((a, i) => ({ value: String(i), label: $t('remote.names.port', { app: a.name, port: a.port }) }))
	);
	let chosen = $derived(status?.apps[Number(choice)] ?? null);
	let moved = $derived(!!status && status.names.length > 0 && !!status.announced && status.address !== status.announced);

	onMount(() => {
		void load();
	});

	/** Where a name opens the app. */
	function urlOf(name: AppName): string {
		// i18n-ignore: a URL
		return `https://${name.name}`;
	}

	async function load() {
		loading = true;
		try {
			status = await getAppNames();
			loadError = '';
		} catch (e) {
			loadError = messageOf(e);
		} finally {
			loading = false;
		}
	}

	/** A label from an app's name: lower case, a-z, 0-9 and -, at most 32 characters. */
	function labelOf(name: string): string {
		return name
			.toLowerCase()
			.replace(/[^a-z0-9-]+/g, '-')
			.replace(/^-+|-+$/g, '')
			.slice(0, 32)
			.replace(/-+$/, '');
	}

	function openAdd() {
		choice = status && status.apps.length > 0 ? '0' : '';
		addError = '';
		showAdd = true;
	}

	// A new choice of app proposes its label, and so does a new status (its apps are new objects),
	// as the statement of Svelte 4 did. It runs when the page updates, before it is drawn: add()
	// still reads the typed label after it set the status, which a label derived from the choice
	// would already have replaced.
	$effect.pre(() => {
		const name = chosen?.name ?? '';
		untrack(() => proposeLabel(name));
	});

	function proposeLabel(name: string) {
		label = labelOf(name);
	}

	async function add() {
		busy = true;
		addError = '';
		error = '';
		success = '';
		try {
			if (!chosen) return;
			status = await addAppName(label.trim(), chosen.name, chosen.port);
			showAdd = false;
			success = $t('remote.names.added', { name: status.names.find((n) => n.label === label.trim())?.name ?? label.trim() });
		} catch (e) {
			addError = messageOf(e);
		} finally {
			busy = false;
		}
	}

	async function remove() {
		const name = removing;
		removing = null;
		if (!name) return;
		busy = true;
		error = '';
		success = '';
		try {
			status = await removeAppName(name.label);
			success = $t('remote.names.removed', { name: name.name });
		} catch (e) {
			error = messageOf(e);
			await load();
		} finally {
			busy = false;
		}
	}

	async function applyAgain() {
		busy = true;
		error = '';
		success = '';
		try {
			status = await applyAppNames();
			success = $t('remote.names.applied');
		} catch (e) {
			error = messageOf(e);
		} finally {
			busy = false;
		}
	}
</script>

<Card>
	<!-- The header: an action that opens a dialog is a secondary button. -->
	<div class="card-header">
		<div class="min-w-0">
			<h3 class="card-title">{$t('remote.names.title')}</h3>
			<p class="card-description break-words">{$t('remote.names.description', { host })}</p>
		</div>
		{#if status?.volume && status.announcer}
			<Button variant="secondary" size="sm" icon={Plus} disabled={busy || status.apps.length === 0}
				title={status.apps.length === 0 ? $t('remote.names.noApps') : undefined} onclick={openAdd}>
				{$t('remote.names.add')}
			</Button>
		{/if}
	</div>

	{#if success}
		<Alert variant="success" class="mb-4"><p class="break-words">{success}</p></Alert>
	{/if}
	{#if error}
		<Alert variant="error" class="mb-4"><p class="break-words">{error}</p></Alert>
	{/if}
	{#if loadError}
		<Alert variant="error" title={$t('remote.names.loadFailed')} class="mb-4">
			<p class="break-words">{loadError}</p>
			<Button variant="secondary" size="sm" class="mt-3" {loading} onclick={load}>{$t('common.retry')}</Button>
		</Alert>
	{/if}

	{#if status && !status.volume}
		<Alert variant="info" class="mt-4">{$t('remote.names.noVolume')}</Alert>
	{:else if status && !status.announcer && status.names.length === 0}
		<Alert variant="info" class="mt-4">{$t('remote.names.noAnnouncer')}</Alert>
	{:else if status}
		{#if moved}
			<Alert variant="warning" class="mt-4">
				<p class="break-words">{$t('remote.names.addressChanged', { announced: status.announced ?? '', address: status.address ?? '' })}</p>
				<Button variant="secondary" size="sm" class="mt-3" disabled={busy} onclick={applyAgain}>{$t('remote.names.apply')}</Button>
			</Alert>
		{/if}
		{#if status.names.length === 0}
			<p class="text-sm text-content-secondary text-center py-4">
				{status.apps.length === 0 ? $t('remote.names.noApps') : $t('remote.names.none')}
			</p>
		{:else}
			<ul class="mt-4 space-y-2" aria-label={$t('remote.names.title')}>
				{#each status.names as name (name.label)}
					<li class="flex items-center justify-between gap-2 p-3 bg-surface-overlay rounded-lg">
						<div class="min-w-0 break-all">
							<p class="font-medium">{urlOf(name)}</p>
							<p class="text-sm text-content-secondary">{$t('remote.names.port', { app: name.app, port: name.port })}</p>
							{#if !name.installed}
								<p class="text-sm text-status-warning">{$t('remote.names.missingApp')}</p>
							{/if}
						</div>
						<Button
							variant="ghost-danger"
							size="icon-sm"
							disabled={busy}
							title={$t('remote.names.removeLabel', { name: name.name })}
							aria-label={$t('remote.names.removeLabel', { name: name.name })}
							onclick={() => (removing = name)}
						>
							<Trash2 class="w-4 h-4" />
						</Button>
					</li>
				{/each}
			</ul>
		{/if}
		<p class="mt-4 text-sm text-content-secondary">{$t('remote.names.trust')}</p>
	{/if}
</Card>

<FormDialog
	open={showAdd}
	title={$t('remote.names.addTitle')}
	formId="app-name-add"
	{busy}
	error={addError}
	submitLabel={busy ? $t('remote.names.adding') : $t('remote.names.add')}
	submitDisabled={busy || !label.trim() || !chosen}
	oncancel={() => (showAdd = false)}
	onsubmit={add}
>
	<Select label={$t('remote.names.app')} options={appOptions} bind:value={choice} />
	<Input
		label={$t('remote.names.label')}
		hint={$t('remote.names.labelHint', { name: label.trim(), host })}
		bind:value={label}
		maxlength={32}
		spellcheck={false}
		required
	/>
</FormDialog>

<ConfirmDialog
	open={removing !== null}
	title={$t('remote.names.remove')}
	danger
	confirmLabel={$t('remote.names.remove')}
	{busy}
	oncancel={() => (removing = null)}
	onconfirm={remove}
>
	<p class="text-sm break-words">{$t('remote.names.removeLabel', { name: removing?.name ?? '' })}</p>
</ConfirmDialog>
