<script lang="ts">
	import { onMount } from 'svelte';
	import { HardDrive, Plus } from 'lucide-svelte';
	import {
		changeIscsiTarget,
		createIscsiTarget,
		deleteIscsiTarget,
		getIscsi,
		type IscsiSecrets,
		type IscsiState,
		type IscsiTarget
	} from '$lib/api/iscsi';
	import type { SharePath } from '$lib/api/shares';
	import { Alert, Badge, Button, Card, EmptyState, Input, Modal, Select } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	type Props = {
		/** Pools and volumes (GET /shares/paths): a target takes a volume of a pool that UNOS manages. */
		paths?: SharePath[];
	};

	let { paths = [] }: Props = $props();

	let iscsiState: IscsiState | null = $state(null);
	let loadError = $state('');
	let notice = $state('');

	// The volumes that can hold a LUN, by "pool/volume".
	const volumes = $derived(paths.filter((p) => p.type === 'volume' && p.managed));
	const volumeOptions = $derived(volumes.map((p) => ({ value: p.name, label: p.name })));

	function messageOf(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

	function lines(text: string): string[] {
		return text
			.split('\n')
			.map((line) => line.trim())
			.filter((line) => line.length > 0);
	}

	async function load() {
		try {
			iscsiState = await getIscsi();
			loadError = '';
		} catch (e) {
			loadError = messageOf(e);
		}
	}

	onMount(load);

	// ---- the secrets, shown once ----
	let shown: IscsiSecrets | null = $state(null);
	let copied = $state(false);
	let copyFailed = $state(false);

	async function copy(text: string | null) {
		if (!text) return;
		try {
			await navigator.clipboard.writeText(text);
			copied = true;
			copyFailed = false;
		} catch {
			copied = false;
			copyFailed = true;
		}
	}

	function closeSecrets() {
		shown = null;
		copied = false;
		copyFailed = false;
	}

	// ---- create ----
	let creating = $state(false);
	let saving = $state(false);
	let attempted = $state(false);
	let formError = $state('');
	let name = $state('');
	let volume = $state('');
	let size = $state('');
	let initiators = $state('');
	let chapUser = $state('');
	let chapSecret = $state('');
	let mutual = $state(false);
	let mutualSecret = $state('');

	const sizeNumber = $derived(Number(size));
	const nameError = $derived(attempted && !name.trim() ? $t('iscsi.form.nameRequired') : '');
	const volumeError = $derived(attempted && !volume ? $t('iscsi.form.volumeRequired') : '');
	const sizeError = $derived(
		attempted && !(Number.isInteger(sizeNumber) && sizeNumber > 0) ? $t('iscsi.form.sizeRequired') : ''
	);
	const initiatorsError = $derived(
		attempted && lines(initiators).length === 0 ? $t('iscsi.form.initiatorsRequired') : ''
	);

	function openCreate() {
		name = '';
		volume = volumes[0]?.name ?? '';
		size = '';
		initiators = '';
		chapUser = '';
		chapSecret = '';
		mutual = false;
		mutualSecret = '';
		attempted = false;
		formError = '';
		creating = true;
	}

	async function create() {
		if (saving) return;
		attempted = true;
		formError = '';
		const number = Number(size);
		if (!name.trim() || !volume || !(Number.isInteger(number) && number > 0) || lines(initiators).length === 0) return;
		const [pool, vol] = volume.split('/');
		saving = true;
		try {
			const result = await createIscsiTarget({
				name: name.trim(),
				pool,
				volume: vol,
				size_gib: number,
				initiators: lines(initiators),
				chap_user: chapUser.trim() || undefined,
				chap_secret: chapSecret || undefined,
				mutual,
				mutual_secret: mutual && mutualSecret ? mutualSecret : undefined
			});
			creating = false;
			notice = $t('iscsi.created', { name: result.target.name });
			shown = result;
			await load();
		} catch (e) {
			formError = messageOf(e);
		} finally {
			saving = false;
		}
	}

	// ---- change ----
	let editing: IscsiTarget | null = $state(null);
	let editInitiators = $state('');
	let newSecret = $state(false);
	let editSecret = $state('');
	let editMutual = $state(false);
	let newMutualSecret = $state(false);
	let editMutualSecret = $state('');
	let editError = $state('');

	function openEdit(target: IscsiTarget) {
		editing = target;
		editInitiators = target.initiators.join('\n');
		newSecret = false;
		editSecret = '';
		editMutual = target.mutual;
		newMutualSecret = false;
		editMutualSecret = '';
		editError = '';
	}

	async function saveEdit() {
		if (!editing || saving) return;
		editError = '';
		const target = editing;
		const turnedOn = editMutual && !target.mutual;
		saving = true;
		try {
			const result = await changeIscsiTarget(target.name, {
				initiators: lines(editInitiators),
				new_secret: newSecret,
				chap_secret: newSecret && editSecret ? editSecret : undefined,
				mutual: editMutual,
				new_mutual_secret: editMutual && newMutualSecret,
				mutual_secret: editMutual && (newMutualSecret || turnedOn) && editMutualSecret ? editMutualSecret : undefined
			});
			editing = null;
			notice = $t('iscsi.changed', { name: target.name });
			if (result.chap_secret || result.mutual_secret) shown = result;
			await load();
		} catch (e) {
			editError = messageOf(e);
		} finally {
			saving = false;
		}
	}

	// ---- delete ----
	let deleting: IscsiTarget | null = $state(null);
	let typed = $state('');
	let deleteError = $state('');

	function openDelete(target: IscsiTarget) {
		deleting = target;
		typed = '';
		deleteError = '';
	}

	async function remove() {
		if (!deleting || saving || typed !== deleting.name) return;
		const target = deleting;
		saving = true;
		deleteError = '';
		try {
			await deleteIscsiTarget(target.name, typed);
			deleting = null;
			notice = $t('iscsi.deleted', { name: target.name });
			await load();
		} catch (e) {
			deleteError = messageOf(e);
		} finally {
			saving = false;
		}
	}
</script>

<div class="space-y-6">
	{#if notice}
		<Alert variant="success" dismissible ondismiss={() => (notice = '')}>{notice}</Alert>
	{/if}

	<Card>
		<div class="card-header flex-wrap gap-2">
			<h2 class="card-title">{$t('iscsi.title')}</h2>
			<Button
				icon={Plus}
				disabled={!iscsiState || !iscsiState.kernel_support || volumes.length === 0}
				onclick={openCreate}
			>
				{$t('iscsi.add')}
			</Button>
		</div>
		<p class="text-sm text-content-secondary mb-4">{$t('iscsi.intro')}</p>

		{#if loadError}
			<Alert variant="error" title={$t('iscsi.loadError')}>{loadError}</Alert>
		{:else if !iscsiState}
			<p class="loading-text">{$t('common.loading')}</p>
		{:else}
			<div class="space-y-4">
				{#if !iscsiState.kernel_support}
					<Alert variant="warning">{$t('iscsi.noKernel')}</Alert>
				{/if}
				{#if volumes.length === 0}
					<Alert variant="info">{$t('iscsi.noVolumes')}</Alert>
				{/if}
				{#if iscsiState.portals_problem}
					<Alert variant="warning" title={$t('iscsi.portalsProblem')}>{iscsiState.portals_problem}</Alert>
				{:else if iscsiState.portals.length > 0}
					<p class="text-sm break-words">
						{$t('iscsi.portals', { portals: iscsiState.portals.join(', ') })}
					</p>
				{:else}
					<Alert variant="warning">{$t('iscsi.noPortals')}</Alert>
				{/if}

				{#if iscsiState.targets.length === 0}
					<EmptyState icon={HardDrive} title={$t('iscsi.empty')} description={$t('iscsi.emptyHint')} />
				{:else}
					<ul class="space-y-3" aria-label={$t('iscsi.title')}>
						{#each iscsiState.targets as target (target.name)}
							<li class="card-nested space-y-2" data-target={target.name}>
								<div class="flex flex-wrap items-center gap-2 justify-between">
									<div class="flex flex-wrap items-center gap-2 min-w-0">
										<h3 class="font-semibold break-all">{target.name}</h3>
										{#if target.served}
											<Badge variant="success">{$t('iscsi.served')}</Badge>
										{:else}
											<Badge variant="warning">{$t('iscsi.notServed')}</Badge>
										{/if}
									</div>
									<div class="flex flex-wrap gap-2">
										<Button variant="secondary" size="sm" onclick={() => openEdit(target)}>
											{$t('iscsi.change')}
										</Button>
										<Button variant="danger" size="sm" onclick={() => openDelete(target)}>
											{$t('iscsi.delete')}
										</Button>
									</div>
								</div>
								{#if target.problem}
									<Alert variant="warning">{target.problem}</Alert>
								{/if}
								<dl class="grid grid-cols-1 sm:grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
									<dt class="text-content-secondary">{$t('iscsi.iqn')}</dt>
									<dd class="mono break-all">{target.iqn}</dd>
									<dt class="text-content-secondary">{$t('iscsi.lun')}</dt>
									<dd class="break-words">
										{$t('iscsi.lunValue', { size: target.size_gib, volume: target.volume, pool: target.pool })}
									</dd>
									<dt class="text-content-secondary">{$t('iscsi.file')}</dt>
									<dd class="mono break-all">{target.path}</dd>
									<dt class="text-content-secondary">{$t('iscsi.initiators')}</dt>
									<dd class="mono break-all">
										{#each target.initiators as initiator}
											<div>{initiator}</div>
										{/each}
									</dd>
									<dt class="text-content-secondary">{$t('iscsi.chapUser')}</dt>
									<dd class="mono break-all">{target.chap_user}</dd>
									<dt class="text-content-secondary">{$t('iscsi.mutual')}</dt>
									<dd>{target.mutual ? $t('iscsi.yes') : $t('iscsi.no')}</dd>
									<dt class="text-content-secondary">{$t('iscsi.sessions')}</dt>
									<dd class="mono break-all">
										{#if target.sessions.length > 0}
											{target.sessions.join(', ')}
										{:else}
											<span class="font-sans text-content-secondary">{$t('iscsi.noSessions')}</span>
										{/if}
									</dd>
								</dl>
							</li>
						{/each}
					</ul>
				{/if}
			</div>
		{/if}
	</Card>
</div>

{#if creating}
	<Modal open title={$t('iscsi.form.title')} size="md" closable={!saving} onclose={() => (creating = false)}>
		<form
			onsubmit={(event) => {
				event.preventDefault();
				create();
			}}
			class="space-y-4"
		>
			<Input
				id="iscsi-name"
				label={$t('iscsi.form.name')}
				bind:value={name}
				autocomplete="off"
				spellcheck={false}
				maxlength={32}
				error={nameError}
				hint={$t('iscsi.form.nameHint')}
			/>
			<Select
				id="iscsi-volume"
				label={$t('iscsi.form.volume')}
				bind:value={volume}
				options={volumeOptions}
				placeholder={$t('iscsi.form.chooseVolume')}
				error={volumeError}
			/>
			<Input
				id="iscsi-size"
				type="number"
				label={$t('iscsi.form.size')}
				bind:value={size}
				error={sizeError}
				hint={$t('iscsi.form.sizeHint')}
			/>
			<div>
				<label for="iscsi-initiators" class="form-label">{$t('iscsi.form.initiators')}</label>
				<textarea
					id="iscsi-initiators"
					class="form-input mono"
					rows="3"
					spellcheck="false"
					autocomplete="off"
					placeholder={$t('iscsi.form.initiatorsPlaceholder')}
					aria-describedby="iscsi-initiators-hint"
					bind:value={initiators}
				></textarea>
				{#if initiatorsError}
					<p class="form-error">{initiatorsError}</p>
				{/if}
				<p id="iscsi-initiators-hint" class="form-hint">{$t('iscsi.form.initiatorsHint')}</p>
			</div>
			<Input
				id="iscsi-chap-user"
				label={$t('iscsi.form.chapUser')}
				bind:value={chapUser}
				autocomplete="off"
				spellcheck={false}
				maxlength={64}
				placeholder={name.trim()}
				hint={$t('iscsi.form.chapUserHint')}
			/>
			<Input
				id="iscsi-chap-secret"
				type="password"
				label={$t('iscsi.form.chapSecret')}
				bind:value={chapSecret}
				autocomplete="new-password"
				maxlength={16}
				hint={$t('iscsi.form.chapSecretHint')}
			/>
			<label class="flex items-center gap-2 cursor-pointer">
				<input type="checkbox" class="form-checkbox" bind:checked={mutual} />
				{$t('iscsi.form.mutual')}
			</label>
			{#if mutual}
				<Input
					id="iscsi-mutual-secret"
					type="password"
					label={$t('iscsi.form.mutualSecret')}
					bind:value={mutualSecret}
					autocomplete="new-password"
					maxlength={16}
					hint={$t('iscsi.form.mutualSecretHint')}
				/>
			{/if}
		</form>
		{#if formError}
			<Alert variant="error" title={$t('iscsi.form.notCreated')}>{formError}</Alert>
		{/if}
		{#snippet footer()}
			<Button variant="secondary" class="flex-1" disabled={saving} onclick={() => (creating = false)}>
				{$t('common.cancel')}
			</Button>
			<Button class="flex-1" loading={saving} onclick={create}>{$t('iscsi.form.create')}</Button>
		{/snippet}
	</Modal>
{/if}

{#if editing}
	<Modal
		open
		title={$t('iscsi.edit.title', { name: editing.name })}
		size="md"
		closable={!saving}
		onclose={() => (editing = null)}
	>
		<form
			onsubmit={(event) => {
				event.preventDefault();
				saveEdit();
			}}
			class="space-y-4"
		>
			<div>
				<label for="iscsi-edit-initiators" class="form-label">{$t('iscsi.form.initiators')}</label>
				<textarea
					id="iscsi-edit-initiators"
					class="form-input mono"
					rows="3"
					spellcheck="false"
					autocomplete="off"
					aria-describedby="iscsi-edit-initiators-hint"
					bind:value={editInitiators}
				></textarea>
				<p id="iscsi-edit-initiators-hint" class="form-hint">{$t('iscsi.form.initiatorsHint')}</p>
			</div>
			<label class="flex items-center gap-2 cursor-pointer">
				<input type="checkbox" class="form-checkbox" bind:checked={newSecret} />
				{$t('iscsi.edit.newSecret')}
			</label>
			{#if newSecret}
				<Input
					id="iscsi-edit-secret"
					type="password"
					label={$t('iscsi.form.chapSecret')}
					bind:value={editSecret}
					autocomplete="new-password"
					maxlength={16}
					hint={$t('iscsi.edit.newSecretHint')}
				/>
			{/if}
			<label class="flex items-center gap-2 cursor-pointer">
				<input type="checkbox" class="form-checkbox" bind:checked={editMutual} />
				{$t('iscsi.edit.mutual')}
			</label>
			{#if editMutual && editing.mutual}
				<label class="flex items-center gap-2 cursor-pointer">
					<input type="checkbox" class="form-checkbox" bind:checked={newMutualSecret} />
					{$t('iscsi.edit.newMutualSecret')}
				</label>
			{/if}
			{#if editMutual && (newMutualSecret || !editing.mutual)}
				<Input
					id="iscsi-edit-mutual-secret"
					type="password"
					label={$t('iscsi.form.mutualSecret')}
					bind:value={editMutualSecret}
					autocomplete="new-password"
					maxlength={16}
					hint={$t('iscsi.form.mutualSecretHint')}
				/>
			{/if}
		</form>
		{#if editError}
			<Alert variant="error" title={$t('iscsi.edit.notChanged')}>{editError}</Alert>
		{/if}
		{#snippet footer()}
			<Button variant="secondary" class="flex-1" disabled={saving} onclick={() => (editing = null)}>
				{$t('common.cancel')}
			</Button>
			<Button class="flex-1" loading={saving} onclick={saveEdit}>{$t('iscsi.edit.save')}</Button>
		{/snippet}
	</Modal>
{/if}

{#if shown}
	<Modal open title={$t('iscsi.secret.title', { name: shown.target.name })} size="md" onclose={closeSecrets}>
		<div class="space-y-4">
			<Alert variant="warning">{$t('iscsi.secret.once')}</Alert>
			<dl class="grid grid-cols-1 gap-y-1 text-sm">
				<dt class="text-content-secondary">{$t('iscsi.iqn')}</dt>
				<dd class="mono break-all">{shown.target.iqn}</dd>
				<dt class="text-content-secondary">{$t('iscsi.chapUser')}</dt>
				<dd class="mono break-all">{shown.target.chap_user}</dd>
			</dl>
			{#if shown.chap_secret}
				<div>
					<label for="iscsi-shown-secret" class="form-label">{$t('iscsi.secret.chap')}</label>
					<div class="flex flex-wrap items-center gap-2">
						<input id="iscsi-shown-secret" class="form-input mono flex-1 min-w-0" readonly value={shown.chap_secret} />
						<Button variant="secondary" size="sm" onclick={() => copy(shown?.chap_secret ?? null)}>
							{$t('iscsi.secret.copy')}
						</Button>
					</div>
				</div>
			{/if}
			{#if shown.mutual_secret}
				<div>
					<label for="iscsi-shown-mutual" class="form-label">{$t('iscsi.secret.mutual')}</label>
					<div class="flex flex-wrap items-center gap-2">
						<input id="iscsi-shown-mutual" class="form-input mono flex-1 min-w-0" readonly value={shown.mutual_secret} />
						<Button variant="secondary" size="sm" onclick={() => copy(shown?.mutual_secret ?? null)}>
							{$t('iscsi.secret.copy')}
						</Button>
					</div>
				</div>
			{/if}
			{#if copied}<p class="text-sm text-status-success">{$t('iscsi.secret.copied')}</p>{/if}
			{#if copyFailed}<p class="text-sm text-status-error">{$t('iscsi.secret.copyFailed')}</p>{/if}
		</div>
		{#snippet footer()}
			<Button class="flex-1" onclick={closeSecrets}>{$t('iscsi.secret.done')}</Button>
		{/snippet}
	</Modal>
{/if}

{#if deleting}
	<Modal
		open
		danger
		title={$t('iscsi.remove.title', { name: deleting.name })}
		size="md"
		closable={!saving}
		onclose={() => (deleting = null)}
	>
		<form
			onsubmit={(event) => {
				event.preventDefault();
				remove();
			}}
			class="space-y-4"
		>
			<p class="text-sm break-words">{$t('iscsi.remove.warning', { path: deleting.path })}</p>
			{#if deleting.sessions.length > 0}
				<Alert variant="warning">
					{$t('iscsi.remove.sessions', { initiators: deleting.sessions.join(', ') })}
				</Alert>
			{/if}
			<Input
				id="iscsi-delete-confirm"
				label={$t('iscsi.remove.type', { name: deleting.name })}
				bind:value={typed}
				autocomplete="off"
				spellcheck={false}
			/>
		</form>
		{#if deleteError}
			<Alert variant="error" title={$t('iscsi.remove.notDeleted')}>{deleteError}</Alert>
		{/if}
		{#snippet footer()}
			<Button variant="secondary" class="flex-1" disabled={saving} onclick={() => (deleting = null)}>
				{$t('common.cancel')}
			</Button>
			<Button
				variant="danger"
				class="flex-1"
				loading={saving}
				disabled={typed !== deleting?.name}
				onclick={remove}
			>
				{$t('iscsi.remove.confirm')}
			</Button>
		{/snippet}
	</Modal>
{/if}
