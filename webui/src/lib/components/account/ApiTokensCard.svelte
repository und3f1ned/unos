<script lang="ts">
	import { messageOf } from '$lib/errors';
	import { onMount } from 'svelte';
	import { KeyRound } from 'lucide-svelte';
	import {
		createApiToken,
		deleteApiToken,
		listApiTokens,
		toggleApiToken,
		type ApiToken,
		type CreatedApiToken
	} from '$lib/api/auth';
	import { Alert, Button, Card, EmptyState } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import ApiTokenItem from './ApiTokenItem.svelte';
	import ApiTokenShownDialog from './ApiTokenShownDialog.svelte';
	import DeleteApiTokenDialog from './DeleteApiTokenDialog.svelte';
	import NewApiTokenDialog from './NewApiTokenDialog.svelte';
	import { ApiTokenForm } from './apiTokenForm.svelte';

	// The API tokens of the signed-in account (ADR-0239), each full or limited to reading and to
	// areas of the API (ADR-0392). The page signs in with a session, which alone makes, disables
	// and deletes tokens (ADR-0253).

	let tokens = $state<ApiToken[]>([]);
	let loading = $state(true);
	let loadError = $state('');
	let notice = $state('');
	let actionError = $state('');
	let busyId = $state<number | null>(null);

	// The dialog of a new token.
	let creating = $state(false);
	const form = new ApiTokenForm();
	let saving = $state(false);
	let createError = $state('');

	// The token just made, shown only now.
	let created = $state<CreatedApiToken | null>(null);
	let copied = $state(false);
	let copyFailed = $state(false);

	let deleting = $state<ApiToken | null>(null);
	let deleteError = $state('');

	onMount(() => {
		void load();
	});

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

	function openCreate() {
		form.reset();
		createError = '';
		creating = true;
	}

	function closeCreate() {
		if (!saving) creating = false;
	}

	async function create() {
		if (saving || !form.canCreate) return;
		saving = true;
		createError = '';
		try {
			created = await createApiToken({
				name: form.name.trim(),
				read_only: form.readOnly,
				areas: form.everyArea ? null : form.chosenAreas,
				expires_in_days: form.lifetime ? Number(form.lifetime) : null
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
</script>

<Card>
	<section aria-labelledby="api-tokens-title">
		<div class="flex flex-wrap items-center justify-between gap-3 mb-4">
			<h3 id="api-tokens-title" class="section-title">
				<KeyRound class="w-5 h-5" />
				{$t('tokens.title')}
			</h3>
			<Button size="sm" onclick={openCreate}>{$t('tokens.new')}</Button>
		</div>
		<p class="text-sm text-secondary mb-4">{$t('tokens.description')}</p>

		{#if notice}
			<Alert variant="success" dismissible class="mb-4" ondismiss={() => (notice = '')}>{notice}</Alert>
		{/if}
		{#if actionError}
			<Alert variant="error" title={$t('tokens.toggleFailed')} class="mb-4">{actionError}</Alert>
		{/if}

		{#if loading}
			<p class="text-sm text-muted">{$t('common.loading')}</p>
		{:else if loadError}
			<Alert variant="error" title={$t('tokens.loadError')}>
				{loadError}
				<Button variant="secondary" size="sm" class="mt-3" onclick={load}>{$t('common.retry')}</Button>
			</Alert>
		{:else if tokens.length === 0}
			<EmptyState icon={KeyRound} title={$t('tokens.none')} />
		{:else}
			<ul class="space-y-3" aria-label={$t('tokens.list')}>
				{#each tokens as token (token.id)}
					<ApiTokenItem
						{token}
						busy={busyId !== null}
						toggling={busyId === token.id && deleting === null}
						ontoggle={() => toggle(token)}
						ondelete={() => askDelete(token)}
					/>
				{/each}
			</ul>
		{/if}
	</section>
</Card>

<NewApiTokenDialog open={creating} {form} {saving} error={createError} oncreate={create} onclose={closeCreate} />

<!-- Closed only by Done: the token is shown only now. -->
<ApiTokenShownDialog token={created} {copied} {copyFailed} oncopy={copy} ondone={() => (created = null)} />

<DeleteApiTokenDialog
	token={deleting}
	busy={busyId !== null}
	error={deleteError}
	ondelete={remove}
	onclose={closeDelete}
/>
