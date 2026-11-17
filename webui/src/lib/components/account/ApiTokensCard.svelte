<script lang="ts">
	import { onMount } from 'svelte';
	import { KeyRound } from 'lucide-svelte';
	import {
		TOKEN_AREAS,
		WARNED_AREAS,
		createApiToken,
		deleteApiToken,
		listApiTokens,
		toggleApiToken,
		type ApiToken,
		type CreatedApiToken,
		type TokenArea
	} from '$lib/api/auth';
	import { Alert, Badge, Button, Card, EmptyState, Input, Modal, Select } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';

	// The API tokens of the signed-in account (ADR-0239), each full or limited to reading and to
	// areas of the API (ADR-0388). The page signs in with a session, which alone makes, disables
	// and deletes tokens (ADR-0253).

	const AREA_LABELS: Record<TokenArea, string> = {
		apps: 'tokens.area.apps',
		audit: 'tokens.area.audit',
		auth: 'tokens.area.auth',
		backup: 'tokens.area.backup',
		containers: 'tokens.area.containers',
		files: 'tokens.area.files',
		groups: 'tokens.area.groups',
		health: 'tokens.area.health',
		logs: 'tokens.area.logs',
		monitor: 'tokens.area.monitor',
		network: 'tokens.area.network',
		notifications: 'tokens.area.notifications',
		offsite: 'tokens.area.offsite',
		power: 'tokens.area.power',
		scheduler: 'tokens.area.scheduler',
		security: 'tokens.area.security',
		shares: 'tokens.area.shares',
		snapshots: 'tokens.area.snapshots',
		storage: 'tokens.area.storage',
		system: 'tokens.area.system',
		updates: 'tokens.area.updates'
	};
	// The lifetimes the dialog offers, in days; '' never expires.
	const LIFETIMES = [30, 90, 365];
	const NAME_CHARS = 64;

	let tokens: ApiToken[] = [];
	let loading = true;
	let loadError = '';
	let notice = '';
	let actionError = '';
	let busyId: number | null = null;

	// The dialog of a new token. It starts at the least that a person can choose: read only.
	let creating = false;
	let name = '';
	let lifetime = '';
	let readOnly = true;
	let everyArea = true;
	let chosen: Partial<Record<TokenArea, boolean>> = {};
	let saving = false;
	let createError = '';

	// The token just made, shown only now.
	let created: CreatedApiToken | null = null;
	let copied = false;
	let copyFailed = false;

	let deleting: ApiToken | null = null;
	let deleteError = '';

	onMount(() => {
		void load();
	});

	function messageOf(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

	async function load() {
		try {
			tokens = await listApiTokens();
			loadError = '';
		} catch (e) {
			loadError = messageOf(e);
		} finally {
			loading = false;
		}
	}

	/** A time as the API answers a token's: UTC, without a zone. */
	function utc(value: string): Date {
		return new Date(/(Z|[+-]\d\d:?\d\d)$/.test(value) ? value : `${value}Z`);
	}

	function formatted(value: string): string {
		return utc(value).toLocaleString($intlLocale);
	}

	// i18n-ignore: a path of the API, the same in every language
	const pathOf = (area: TokenArea) => `/api/v1/${area}`;

	function areaNames(areas: readonly TokenArea[]): string {
		return areas.map((area) => $t(AREA_LABELS[area])).join(', ');
	}

	function accessOf(token: { read_only: boolean; areas: TokenArea[] | null }): string {
		if (!token.read_only && token.areas === null) return $t('tokens.access.full');
		return token.read_only ? $t('tokens.access.read') : $t('tokens.access.change');
	}

	function expired(token: ApiToken): boolean {
		return token.expires_at !== null && utc(token.expires_at).getTime() < Date.now();
	}

	function openCreate() {
		name = '';
		lifetime = '';
		readOnly = true;
		everyArea = true;
		chosen = {};
		createError = '';
		creating = true;
	}

	function closeCreate() {
		if (!saving) creating = false;
	}

	async function create() {
		if (saving || !canCreate) return;
		saving = true;
		createError = '';
		try {
			created = await createApiToken({
				name: name.trim(),
				read_only: readOnly,
				areas: everyArea ? null : chosenAreas,
				expires_in_days: lifetime ? Number(lifetime) : null
			});
			copied = false;
			copyFailed = false;
			creating = false;
			await load();
		} catch (e) {
			createError = messageOf(e);
		} finally {
			saving = false;
		}
	}

	async function copy() {
		if (!created) return;
		try {
			await navigator.clipboard.writeText(created.token);
			copied = true;
			copyFailed = false;
		} catch {
			copyFailed = true;
		}
	}

	async function toggle(token: ApiToken) {
		if (busyId !== null) return;
		busyId = token.id;
		actionError = '';
		notice = '';
		try {
			await toggleApiToken(token.id);
			await load();
		} catch (e) {
			actionError = messageOf(e);
		} finally {
			busyId = null;
		}
	}

	function askDelete(token: ApiToken) {
		deleteError = '';
		deleting = token;
	}

	function closeDelete() {
		if (busyId === null) deleting = null;
	}

	async function remove() {
		if (!deleting || busyId !== null) return;
		const token = deleting;
		busyId = token.id;
		deleteError = '';
		try {
			await deleteApiToken(token.id);
			deleting = null;
			notice = $t('tokens.deleted', { name: token.name });
			actionError = '';
			await load();
		} catch (e) {
			deleteError = messageOf(e);
		} finally {
			busyId = null;
		}
	}

	$: chosenAreas = TOKEN_AREAS.filter((area) => chosen[area]);
	$: canCreate = name.trim().length > 0 && (everyArea || chosenAreas.length > 0);
	// Change access to chosen areas whose changes reach beyond them (WARNED_AREAS). Change access to
	// every area is a full token, as the choice says.
	$: warned = !readOnly && !everyArea && chosenAreas.some((area) => WARNED_AREAS.includes(area));
	$: lifetimeOptions = [
		{ value: '', label: $t('tokens.form.never') },
		...LIFETIMES.map((days) => ({ value: String(days), label: $t('tokens.form.afterDays', { n: days }) }))
	];
</script>

<Card>
	<section aria-labelledby="api-tokens-title">
		<div class="flex flex-wrap items-center justify-between gap-3 mb-4">
			<h3 id="api-tokens-title" class="section-title">
				<KeyRound class="w-5 h-5" />
				{$t('tokens.title')}
			</h3>
			<Button size="sm" on:click={openCreate}>{$t('tokens.new')}</Button>
		</div>
		<p class="text-sm text-secondary mb-4">{$t('tokens.description')}</p>

		{#if notice}
			<Alert variant="success" dismissible class="mb-4" on:dismiss={() => (notice = '')}>{notice}</Alert>
		{/if}
		{#if actionError}
			<Alert variant="error" title={$t('tokens.toggleFailed')} class="mb-4">{actionError}</Alert>
		{/if}

		{#if loading}
			<p class="text-sm text-muted">{$t('common.loading')}</p>
		{:else if loadError}
			<Alert variant="error" title={$t('tokens.loadError')}>
				{loadError}
				<Button variant="secondary" size="sm" class="mt-3" on:click={load}>{$t('common.retry')}</Button>
			</Alert>
		{:else if tokens.length === 0}
			<EmptyState icon={KeyRound} title={$t('tokens.none')} />
		{:else}
			<ul class="space-y-3" aria-label={$t('tokens.list')}>
				{#each tokens as token (token.id)}
					<li class="card-nested p-3 space-y-2 min-w-0" aria-label={token.name}>
						<div class="flex flex-wrap items-center gap-2">
							<span class="font-medium break-all">{token.name}</span>
							<span class="mono text-xs text-muted break-all">{token.token_prefix}</span>
							{#if !token.scope_known}
								<Badge variant="error">{$t('tokens.unknownScope')}</Badge>
							{:else}
								<Badge variant={token.read_only ? 'info' : token.areas === null ? 'warning' : 'neutral'}>
									{accessOf(token)}
								</Badge>
							{/if}
							{#if !token.is_active}
								<Badge variant="neutral">{$t('tokens.disabled')}</Badge>
							{/if}
							{#if expired(token)}
								<Badge variant="warning">{$t('tokens.expired')}</Badge>
							{/if}
						</div>
						{#if token.scope_known}
							<p class="text-sm break-words">
								{token.areas === null
									? $t('tokens.areas.every')
									: $t('tokens.areas.some', { areas: areaNames(token.areas) })}
							</p>
						{:else}
							<p class="text-sm text-error">{$t('tokens.unknownScopeHint')}</p>
						{/if}
						<p class="flex flex-wrap gap-x-3 text-xs text-muted">
							{#if token.created_at}
								<span>{$t('tokens.createdOn', { date: formatted(token.created_at) })}</span>
							{/if}
							<span>
								{token.expires_at
									? $t('tokens.expiresOn', { date: formatted(token.expires_at) })
									: $t('tokens.neverExpires')}
							</span>
							<span>
								{token.last_used_at
									? $t('tokens.lastUsed', { date: formatted(token.last_used_at) })
									: $t('tokens.neverUsed')}
							</span>
						</p>
						<div class="flex flex-wrap gap-2" role="group" aria-label={$t('tokens.actionsOf', { name: token.name })}>
							<Button
								variant="secondary"
								size="sm"
								loading={busyId === token.id && deleting === null}
								disabled={busyId !== null}
								on:click={() => toggle(token)}
							>
								{token.is_active ? $t('tokens.disable') : $t('tokens.enable')}
							</Button>
							<Button variant="ghost" size="sm" disabled={busyId !== null} on:click={() => askDelete(token)}>
								{$t('tokens.delete')}
							</Button>
						</div>
					</li>
				{/each}
			</ul>
		{/if}
	</section>
</Card>

<Modal open={creating} title={$t('tokens.form.title')} size="lg" closable={!saving} on:close={closeCreate}>
	<form id="new-api-token" class="space-y-4" on:submit|preventDefault={create}>
		<Input
			id="api-token-name"
			label={$t('tokens.form.name')}
			hint={$t('tokens.form.nameHint')}
			bind:value={name}
			maxlength={NAME_CHARS}
			required
			disabled={saving}
		/>
		<Select id="api-token-lifetime" label={$t('tokens.form.expiry')} bind:value={lifetime} options={lifetimeOptions} />

		<fieldset class="space-y-2 min-w-0">
			<legend class="form-label">{$t('tokens.form.access')}</legend>
			<label class="flex items-start gap-2 cursor-pointer">
				<input
					type="radio"
					name="api-token-access"
					class="mt-1 border-border bg-surface-overlay text-blue-600 focus:ring-blue-500"
					value={true}
					bind:group={readOnly}
					disabled={saving}
				/>
				<span>
					<span class="block">{$t('tokens.access.read')}</span>
					<span class="block text-sm text-muted">{$t('tokens.form.readHint')}</span>
				</span>
			</label>
			<label class="flex items-start gap-2 cursor-pointer">
				<input
					type="radio"
					name="api-token-access"
					class="mt-1 border-border bg-surface-overlay text-blue-600 focus:ring-blue-500"
					value={false}
					bind:group={readOnly}
					disabled={saving}
				/>
				<span>
					<span class="block">{$t('tokens.access.change')}</span>
					<span class="block text-sm text-muted">{$t('tokens.form.changeHint')}</span>
				</span>
			</label>
		</fieldset>

		<fieldset class="space-y-2 min-w-0">
			<legend class="form-label">{$t('tokens.form.areas')}</legend>
			<label class="flex items-start gap-2 cursor-pointer">
				<input
					type="radio"
					name="api-token-areas"
					class="mt-1 border-border bg-surface-overlay text-blue-600 focus:ring-blue-500"
					value={true}
					bind:group={everyArea}
					disabled={saving}
				/>
				<span>
					<span class="block">{$t('tokens.areas.every')}</span>
					<span class="block text-sm text-muted">{$t('tokens.form.everyHint')}</span>
				</span>
			</label>
			<label class="flex items-start gap-2 cursor-pointer">
				<input
					type="radio"
					name="api-token-areas"
					class="mt-1 border-border bg-surface-overlay text-blue-600 focus:ring-blue-500"
					value={false}
					bind:group={everyArea}
					disabled={saving}
				/>
				<span>{$t('tokens.form.someAreas')}</span>
			</label>
			{#if !everyArea}
				<ul class="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1 pl-6">
					{#each TOKEN_AREAS as area (area)}
						<li class="min-w-0">
							<label class="flex flex-wrap items-center gap-x-2 cursor-pointer">
								<input
									type="checkbox"
									class="form-checkbox"
									checked={chosen[area] === true}
									disabled={saving}
									on:change={(event) => (chosen = { ...chosen, [area]: event.currentTarget.checked })}
								/>
								<span>{$t(AREA_LABELS[area])}</span>
								<span class="mono text-xs text-muted break-all">{pathOf(area)}</span>
							</label>
						</li>
					{/each}
				</ul>
				{#if chosenAreas.length === 0}
					<p class="form-hint">{$t('tokens.form.chooseArea')}</p>
				{/if}
			{/if}
		</fieldset>

		{#if readOnly || !everyArea}
			<p class="form-hint">{$t('tokens.form.limitedNote')}</p>
		{/if}
		{#if warned}
			<Alert variant="warning">{$t('tokens.form.warnedAreas')}</Alert>
		{/if}
		{#if createError}
			<Alert variant="error" title={$t('tokens.form.failed')}>{createError}</Alert>
		{/if}
	</form>

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={saving} on:click={closeCreate}>
			{$t('common.cancel')}
		</Button>
		<Button class="flex-1" loading={saving} disabled={!canCreate} on:click={create}>
			{$t('tokens.form.create')}
		</Button>
	</svelte:fragment>
</Modal>

<!-- Closed only by Done: the token is shown only now. -->
<Modal open={created !== null} title={$t('tokens.shown.title')} size="md" closable={false}>
	{#if created}
		<div class="space-y-3">
			<Alert variant="warning">{$t('tokens.shown.save')}</Alert>
			<p class="text-sm break-words">
				{$t('tokens.shown.summary', {
					name: created.name,
					access: accessOf(created),
					areas:
						created.areas === null
							? $t('tokens.areas.every')
							: $t('tokens.areas.some', { areas: areaNames(created.areas) })
				})}
			</p>
			<div>
				<label for="new-api-token-value" class="form-label">{$t('tokens.shown.token')}</label>
				<textarea
					id="new-api-token-value"
					class="form-input mono break-all resize-none"
					rows="2"
					readonly
					value={created.token}
				/>
			</div>
			<div class="flex flex-wrap items-center gap-2">
				<Button variant="secondary" size="sm" on:click={copy}>{$t('tokens.shown.copy')}</Button>
				{#if copied}<span class="text-sm text-success">{$t('tokens.shown.copied')}</span>{/if}
			</div>
			{#if copyFailed}
				<Alert variant="error">{$t('tokens.shown.copyFailed')}</Alert>
			{/if}
		</div>
	{/if}
	<svelte:fragment slot="footer">
		<Button class="flex-1" on:click={() => (created = null)}>{$t('common.done')}</Button>
	</svelte:fragment>
</Modal>

<Modal
	open={deleting !== null}
	title={$t('tokens.deleteDialog.title')}
	size="md"
	danger
	closable={busyId === null}
	on:close={closeDelete}
>
	{#if deleting}
		<p class="break-words">{$t('tokens.deleteDialog.text', { name: deleting.name })}</p>
		{#if deleteError}
			<Alert variant="error" title={$t('tokens.deleteDialog.failed')} class="mt-3">{deleteError}</Alert>
		{/if}
	{/if}
	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={busyId !== null} on:click={closeDelete}>
			{$t('common.cancel')}
		</Button>
		<Button variant="danger" class="flex-1" loading={busyId !== null} on:click={remove}>{$t('tokens.delete')}</Button>
	</svelte:fragment>
</Modal>
