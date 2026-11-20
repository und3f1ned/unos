<script lang="ts">
	import { onMount } from 'svelte';
	import { HardDrive } from 'lucide-svelte';
	import {
		changeIscsiTarget,
		createIscsiTarget,
		deleteIscsiTarget,
		getIscsi,
		type IscsiSecrets,
		type IscsiState,
		type IscsiTarget,
		type IscsiTargetChange,
		type IscsiTargetCreate
	} from '$lib/api/iscsi';
	import type { SharePath } from '$lib/api/shares';
	import { Alert, Button, Card, EmptyState, Skeleton } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { messageOf } from './errors';
	import IscsiCreateDialog from './IscsiCreateDialog.svelte';
	import IscsiDeleteDialog from './IscsiDeleteDialog.svelte';
	import IscsiEditDialog from './IscsiEditDialog.svelte';
	import IscsiSecretsDialog from './IscsiSecretsDialog.svelte';
	import IscsiTargetItem from './IscsiTargetItem.svelte';

	type Props = {
		/** Pools and volumes (GET /shares/paths): a target takes a volume of a pool that UNOS manages. */
		paths?: SharePath[];
		/** The targets and the host's support as last read; the page's Add target reads it. */
		iscsiState?: IscsiState | null;
	};

	let { paths = [], iscsiState = $bindable(null) }: Props = $props();
	let loadError = $state('');
	let notice = $state('');

	// The volumes that can hold a LUN, by "pool/volume".
	const volumes = $derived(paths.filter((p) => p.type === 'volume' && p.managed));

	async function load() {
		try {
			iscsiState = await getIscsi();
			loadError = '';
		} catch (e) {
			loadError = messageOf(e);
		}
	}

	onMount(load);

	// The dialogs, each mounted while open. One request runs at a time: creating, changing or
	// deleting a target, and the list read again after it; every dialog waits for it.
	let creating = $state(false);
	let editing: IscsiTarget | null = $state(null);
	let deleting: IscsiTarget | null = $state(null);
	// The secrets, shown once.
	let shown: IscsiSecrets | null = $state(null);
	let saving = $state(false);
	// A dialog asked again while it is open (with a button of the list behind it) starts over.
	let createDialog: ReturnType<typeof IscsiCreateDialog> | undefined = $state();
	let editDialog: ReturnType<typeof IscsiEditDialog> | undefined = $state();
	let deleteDialog: ReturnType<typeof IscsiDeleteDialog> | undefined = $state();

	/** The page header's Add target: opens the form of a new target. */
	export function openCreate() {
		createDialog?.reset();
		creating = true;
	}

	function openEdit(target: IscsiTarget) {
		editDialog?.reset(target);
		editing = target;
	}

	function openDelete(target: IscsiTarget) {
		deleteDialog?.reset();
		deleting = target;
	}

	// The requests of the dialogs: a failure rejects, and the dialog shows the server's error.
	async function create(target: IscsiTargetCreate) {
		saving = true;
		try {
			const result = await createIscsiTarget(target);
			creating = false;
			notice = $t('iscsi.created', { name: result.target.name });
			shown = result;
			await load();
		} finally {
			saving = false;
		}
	}

	async function saveEdit(change: IscsiTargetChange) {
		if (!editing) return;
		const target = editing;
		saving = true;
		try {
			const result = await changeIscsiTarget(target.name, change);
			editing = null;
			notice = $t('iscsi.changed', { name: target.name });
			if (result.chap_secret || result.mutual_secret) shown = result;
			await load();
		} finally {
			saving = false;
		}
	}

	async function remove(typed: string) {
		if (!deleting) return;
		const target = deleting;
		saving = true;
		try {
			await deleteIscsiTarget(target.name, typed);
			deleting = null;
			notice = $t('iscsi.deleted', { name: target.name });
			await load();
		} finally {
			saving = false;
		}
	}
</script>

<section class="space-y-6" aria-label={$t('iscsi.title')}>
	{#if notice}
		<Alert variant="success" dismissible ondismiss={() => (notice = '')}>{notice}</Alert>
	{/if}

	{#if loadError}
		<Alert variant="error" title={$t('iscsi.loadError')}>
			<p>{loadError}</p>
			<Button variant="secondary" size="sm" class="mt-3" onclick={load}>{$t('common.retry')}</Button>
		</Alert>
	{:else if !iscsiState}
		<!-- The first load (ADR-0430): the portals' line and the targets. -->
		<div class="space-y-4" aria-busy="true">
			<Skeleton class="w-2/3" />
			<Skeleton shape="card" count={4} />
		</div>
	{:else}
		<div class="space-y-4">
			{#if !iscsiState.kernel_support}
				<Alert variant="warning">{$t('iscsi.noKernel')}</Alert>
			{/if}
			{#if iscsiState.portals_problem}
				<Alert variant="warning" title={$t('iscsi.portalsProblem')}>{iscsiState.portals_problem}</Alert>
			{:else if iscsiState.portals.length === 0}
				<Alert variant="warning">{$t('iscsi.noPortals')}</Alert>
			{/if}

			{#if iscsiState.targets.length === 0}
				<Card>
					<!-- Its next step is the header's Add target; without a volume, Storage. -->
					<EmptyState icon={HardDrive} title={$t('iscsi.empty')} description={$t('iscsi.emptyHint')}>
						{#if iscsiState.kernel_support && volumes.length === 0}
							<a class="link" href="/storage">{$t('shares.toStorage')}</a>
						{/if}
					</EmptyState>
				</Card>
			{:else}
				<ul class="space-y-4" aria-label={$t('iscsi.title')}>
					{#each iscsiState.targets as target (target.name)}
						<IscsiTargetItem {target} onedit={openEdit} ondelete={openDelete} />
					{/each}
				</ul>
			{/if}
			<!-- What an iSCSI target is, and where initiators connect: one line under the targets. -->
			<p class="form-hint break-words">
				{$t('iscsi.intro')}
				{#if !iscsiState.portals_problem && iscsiState.portals.length > 0}
					{' '}{$t('iscsi.portals', { portals: iscsiState.portals.join(', ') })}
				{/if}
			</p>
		</div>
	{/if}
</section>

{#if creating}
	<IscsiCreateDialog bind:this={createDialog} {volumes} {saving} onclose={() => (creating = false)} oncreate={create} />
{/if}

{#if editing}
	<IscsiEditDialog bind:this={editDialog} target={editing} {saving} onclose={() => (editing = null)} onsave={saveEdit} />
{/if}

{#if shown}
	<IscsiSecretsDialog secrets={shown} onclose={() => (shown = null)} />
{/if}

{#if deleting}
	<IscsiDeleteDialog bind:this={deleteDialog} target={deleting} {saving} onclose={() => (deleting = null)} onremove={remove} />
{/if}
