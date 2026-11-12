<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { Edit, Trash2, Wrench } from 'lucide-svelte';
	import type { ShareAccess, SmbShare } from '$lib/api/shares';
	import { Alert, Badge, Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { smbUrl, windowsAddress } from './texts';

	export let share: SmbShare;
	/** The NAS's address as the browser reached it. */
	export let host = '';
	/** The import notes are being dismissed. */
	export let dismissing = false;
	/** A repair of the share's permissions runs (ADR-0363). */
	export let repairing = false;

	const dispatch = createEventDispatcher<{
		edit: SmbShare;
		repair: SmbShare;
		delete: SmbShare;
		dismissNotes: SmbShare;
	}>();

	$: writers = share.access.filter((entry) => entry.level === 'write');
	$: readers = share.access.filter((entry) => entry.level === 'read');
	$: groups = [
		{ label: $t('shares.level.write'), entries: writers },
		{ label: $t('shares.card.read'), entries: readers }
	].filter((group) => group.entries.length > 0);
	$: someNotEffective = share.access.some((entry) => !entry.effective);

	/** Why an access entry does not count yet (it is not in smb.conf). */
	function reason(entry: ShareAccess): string {
		if (!share.served) return $t('shares.card.reason.notServed');
		const { username } = entry;
		switch (entry.smb_state) {
			case 'password_needed':
				return $t('shares.card.reason.passwordNeeded', { username });
			case 'off':
				return $t('shares.card.reason.off', { username });
			case 'unavailable':
				return $t('shares.card.reason.unavailable', { username });
			default:
				return $t('shares.card.reason.other', { username });
		}
	}
</script>

<div class="card-nested space-y-3">
	<div class="flex flex-wrap items-start justify-between gap-3">
		<div class="min-w-0 space-y-1">
			<div class="flex flex-wrap items-center gap-2">
				<h3 class="font-semibold">{share.name}</h3>
				{#if !share.served}
					<Badge variant="error">{$t('shares.card.notServed')}</Badge>
				{/if}
				{#if share.guest_ok}
					<Badge variant="warning">{$t('shares.card.guest')}</Badge>
				{/if}
				{#if !share.browseable}
					<Badge variant="neutral">{$t('shares.card.hidden')}</Badge>
				{/if}
				{#if share.served && !share.available}
					<Badge variant="error">{$t('shares.card.noAccess')}</Badge>
				{/if}
				{#if share.pool_mounted === false}
					<Badge variant="error">{$t('shares.card.poolNotMounted')}</Badge>
				{/if}
				{#if share.permissions_ok === false}
					<Badge variant="warning">{$t('shares.card.permissions')}</Badge>
				{/if}
				{#if repairing}
					<Badge variant="info">{$t('shares.card.repairing')}</Badge>
				{/if}
			</div>
			<p class="mono-xs text-muted break-all">{share.path}</p>
			{#if !share.served && share.path_problem}
				<p class="text-sm text-error">{share.path_problem}</p>
			{/if}
			{#if share.comment}
				<p class="text-sm text-secondary">{share.comment}</p>
			{/if}
		</div>
		<div class="flex flex-wrap items-center gap-2">
			<Button variant="secondary" size="sm" icon={Edit} on:click={() => dispatch('edit', share)}>
				{$t('shares.card.edit')}<span class="sr-only"
					>{` ${$t('shares.card.editOf', { name: share.name })}`}</span
				>
			</Button>
			{#if share.served}
				<Button
					variant="secondary"
					size="sm"
					icon={Wrench}
					on:click={() => dispatch('repair', share)}
				>
					{$t('shares.card.repair')}<span class="sr-only"
						>{` ${$t('shares.card.repairOf', { name: share.name })}`}</span
					>
				</Button>
			{/if}
			<Button variant="ghost" size="icon-sm" on:click={() => dispatch('delete', share)}>
				<Trash2 class="w-4 h-4 text-error" />
				<span class="sr-only">{$t('shares.card.delete', { name: share.name })}</span>
			</Button>
		</div>
	</div>

	<div class="text-sm space-y-1">
		{#if groups.length === 0}
			<p class="text-muted">
				{share.guest_ok ? $t('shares.card.nobodyOwn') : $t('shares.card.nobody')}
			</p>
		{:else}
			<!-- "Read & write: alice, bob · Read: carol"; entries that do not count are crossed out,
			     with the reason as hover text and as text for screen readers (not <s>: it means "deleted"). -->
			<p>
				{#each groups as group, g}{#if g > 0}<span class="text-muted">{' · '}</span>{/if}<span
						class="text-secondary">{group.label}:</span
					>{' '}{#each group.entries as entry, i}{#if i > 0}{', '}{/if}{#if entry.effective}{entry.username}{:else}<span
								class="line-through text-muted"
								title={reason(entry)}>{entry.username}</span
							><span class="sr-only"
								>{` ${$t('shares.card.notActive', { reason: reason(entry) })}`}</span
							>{/if}{/each}{/each}
			</p>
			{#if someNotEffective}
				<p class="text-xs text-muted">
					{share.served ? $t('shares.card.crossedOut') : $t('shares.card.crossedOutNotServed')}
				</p>
			{/if}
		{/if}
		{#if share.served}
			<p class="text-secondary">
				{$t('shares.card.connect')}
				<span class="mono-xs">{windowsAddress(host, share.name)}</span><span class="text-muted"
					>{' · '}</span
				><span class="mono-xs">{smbUrl(host, share.name)}</span>
			</p>
		{/if}
	</div>

	{#if share.import_notes.length > 0}
		<Alert variant="info" title={$t('shares.card.importNotes')}>
			<ul class="list-disc ml-5 space-y-1 text-sm">
				{#each share.import_notes as note}
					<li>{note}</li>
				{/each}
			</ul>
			<div class="mt-2">
				<Button
					variant="secondary"
					size="sm"
					loading={dismissing}
					on:click={() => dispatch('dismissNotes', share)}
				>
					{$t('shares.card.dismiss')}<span class="sr-only"
						>{` ${$t('shares.card.dismissOf', { name: share.name })}`}</span
					>
				</Button>
			</div>
		</Alert>
	{/if}
</div>
