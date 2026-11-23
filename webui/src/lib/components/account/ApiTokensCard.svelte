<script lang="ts">
	import { messageOf } from '$lib/errors';
	import { KeyRound } from 'lucide-svelte';
	import {
		createApiToken,
		deleteApiToken,
		listApiTokens,
		toggleApiToken,
		type ApiToken,
		type CreatedApiToken
	} from '$lib/api/auth';
	import { Alert, Button, Card, EmptyState, Skeleton, LoadState, confirm, toast } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { settle, type Settled } from '$lib/pages/read';
	import ApiTokenItem from './ApiTokenItem.svelte';
	import ApiTokenShownDialog from './ApiTokenShownDialog.svelte';
	import NewApiTokenDialog from './NewApiTokenDialog.svelte';
	import { ApiTokenForm } from './apiTokenForm.svelte';

	// The API tokens of the signed-in account (ADR-0239), each full or limited to reading and to
	// areas of the API (ADR-0392). The page signs in with a session, which alone makes, disables
	// and deletes tokens (ADR-0253). The page holds the list (its load reads it,
	// $lib/pages/account), so that the tab shows it again at once; the card's own reads give it
	// their answers.

	type Props = {
		/** The tokens as last read, or why they could not be; null until the page has read them. */
		read: Settled<ApiToken[]> | null;
		/** A read answered. */
		onchange: (read: Settled<ApiToken[]>) => void;
	};

	let { read, onchange }: Props = $props();

	const tokens = $derived(read?.ok ? read.value : []);
	const loadError = $derived(read && !read.ok ? read.error : '');
	let actionError = $state('');
	let busyId = $state<number | null>(null);
	// The token whose deletion runs: its Enable or Disable does not spin meanwhile.
	let deletingId = $state<number | null>(null);

	// The dialog of a new token.
	let creating = $state(false);
	const form = new ApiTokenForm();
	let saving = $state(false);
	let createError = $state('');

	// The token just made, shown only now.
	let created = $state<CreatedApiToken | null>(null);
	let copyFailed = $state(false);

	async function load() {
		onchange(await settle(listApiTokens()));
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
			toast.success($t('tokens.shown.copied'));
			copyFailed = false;
		} catch {
			copyFailed = true;
		}
	}

	async function toggle(token: ApiToken) {
		if (busyId !== null) return;
		busyId = token.id;
		actionError = '';
		try {
			await toggleApiToken(token.id);
			await load();
		} catch (e) {
			actionError = messageOf(e);
		} finally {
			busyId = null;
		}
	}

	/** Delete a token once confirmed: a program that uses it can no longer sign in. */
	async function askDelete(token: ApiToken) {
		if (busyId !== null) return;
		const yes = await confirm({
			title: $t('tokens.deleteDialog.title', { name: token.name }),
			message: $t('tokens.deleteDialog.text', { name: token.name }),
			confirmLabel: $t('tokens.delete')
		});
		if (!yes) return;
		busyId = deletingId = token.id;
		try {
			await deleteApiToken(token.id);
			toast.success($t('tokens.deleted', { name: token.name }));
			actionError = '';
			await load();
		} catch (e) {
			toast.error(messageOf(e), { title: $t('tokens.deleteDialog.failed') });
		} finally {
			busyId = deletingId = null;
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
			<Button variant="secondary" size="sm" onclick={openCreate}>{$t('tokens.new')}</Button>
		</div>
		<p class="text-sm text-secondary mb-4">{$t('tokens.description')}</p>

		{#if actionError}
			<Alert variant="error" title={$t('tokens.toggleFailed')} class="mb-4">{actionError}</Alert>
		{/if}

		<LoadState
			loading={read === null}
			error={loadError}
			errorTitle={$t('tokens.loadError')}
			empty={tokens.length === 0}
			onretry={load}
		>
			{#snippet skeleton()}<Skeleton count={2} />{/snippet}
			{#snippet emptyState()}<EmptyState compact icon={KeyRound} title={$t('tokens.none')} />{/snippet}
			<ul class="space-y-3" aria-label={$t('tokens.list')}>
				{#each tokens as token (token.id)}
					<ApiTokenItem
						{token}
						busy={busyId !== null}
						toggling={busyId === token.id && deletingId !== token.id}
						ontoggle={() => toggle(token)}
						ondelete={() => askDelete(token)}
					/>
				{/each}
			</ul>
		</LoadState>
	</section>
</Card>

<NewApiTokenDialog open={creating} {form} {saving} error={createError} oncreate={create} onclose={closeCreate} />

<!-- Closed only by Done: the token is shown only now. -->
<ApiTokenShownDialog token={created} {copyFailed} oncopy={copy} ondone={() => (created = null)} />
