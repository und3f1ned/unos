<script lang="ts">
	import { Check, Copy, Edit, Trash2, Wrench } from 'lucide-svelte';
	import type { ShareAccess, ShareGroupMember, SmbShare } from '$lib/api/shares';
	import { Alert, Badge, Button, Checkbox, HelpText, toast } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { effectiveLevel } from './access';
	import { smbUrl, windowsAddress } from './texts';

	type Setting = keyof SmbShare['not_rendered'];
	const OFF_SETTINGS: Setting[] = ['guest_ok', 'time_machine', 'previous_versions'];
	// A stored setting that smb.conf leaves out: one line, and the server's reason under Details.
	const OFF_KEYS: Record<Setting, string> = {
		guest_ok: 'shares.card.off.guest',
		time_machine: 'shares.card.off.timeMachine',
		previous_versions: 'shares.card.off.previousVersions'
	};

	type Props = {
		share: SmbShare;
		/** The NAS's address as the browser reached it. */
		host?: string;
		/** The import notes are being dismissed. */
		dismissing?: boolean;
		/** A repair of the share's permissions runs (ADR-0363). */
		repairing?: boolean;
		/** Selected for an action on several shares (ADR-0389); `onselect` says when its box changes. */
		selected?: boolean;
		selectDisabled?: boolean;
		// The buttons of the card, each with the share.
		onedit?: (share: SmbShare) => void;
		onrepair?: (share: SmbShare) => void;
		ondelete?: (share: SmbShare) => void;
		ondismissnotes?: (share: SmbShare) => void;
		// The box of the selection changed.
		onselect?: (change: { share: SmbShare; selected: boolean }) => void;
	};

	let {
		share,
		host = '',
		dismissing = false,
		repairing = false,
		selected = false,
		selectDisabled = false,
		onedit,
		onrepair,
		ondelete,
		ondismissnotes,
		onselect
	}: Props = $props();

	const writers = $derived(share.access.filter((entry) => entry.level === 'write'));
	const readers = $derived(share.access.filter((entry) => entry.level === 'read'));
	const groups = $derived(
		[
			{ label: $t('shares.level.write'), entries: writers },
			{ label: $t('shares.card.read'), entries: readers }
		].filter((group) => group.entries.length > 0)
	);
	// Groups: each member gets the group's level, or the higher level of their own.
	const groupAccess = $derived(share.group_access ?? []);
	// Users whose own level is Read but who write through a group: the higher level counts.
	const raised = $derived(
		share.access.filter(
			(entry) => entry.level === 'read' && effectiveLevel(entry.username, share.access, groupAccess) === 'write'
		)
	);
	// The stored settings that smb.conf leaves out, with the server's reason (ADR-0380).
	const off: SmbShare['not_rendered'] = $derived(share.not_rendered ?? {});
	const offSettings = $derived(
		OFF_SETTINGS.filter((setting) => off[setting]).map((setting) => ({
			setting,
			text: $t(OFF_KEYS[setting]),
			reason: off[setting] ?? ''
		}))
	);
	// The features of the share: facts, not states, so outlined (ADR-0432).
	const features = $derived(
		[
			share.guest_ok ? $t('shares.card.guest') : '',
			!share.browseable ? $t('shares.card.hidden') : '',
			share.encryption_required ? $t('shares.card.encryption') : '',
			share.time_machine ? $t('shares.card.timeMachine') : '',
			share.previous_versions ? $t('shares.card.previousVersions') : ''
		].filter(Boolean)
	);

	/** Why an access entry does not count yet (it is not in smb.conf). */
	function reason(entry: ShareAccess | ShareGroupMember): string {
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

	/** The short tag after a name that does not count yet; none while the share is not served (its badge says so). */
	function tag(entry: ShareAccess | ShareGroupMember): string {
		if (!share.served) return '';
		switch (entry.smb_state) {
			case 'password_needed':
				return $t('shares.card.tag.passwordNeeded');
			case 'off':
				return $t('shares.card.tag.off');
			case 'unavailable':
				return $t('shares.card.tag.unavailable');
			default:
				return $t('shares.card.tag.other');
		}
	}

	// The address copied last, shown as copied for a moment.
	let copied = $state('');
	let copiedTimer: ReturnType<typeof setTimeout> | undefined;

	// A failed copy, which stays until the next copy: the address is to be copied by hand. A copy
	// that worked is a toast.
	let copyMessage = $state('');

	async function copy(text: string) {
		clearTimeout(copiedTimer);
		try {
			// Undefined over plain HTTP (not a secure context): a failure too.
			await navigator.clipboard.writeText(text);
		} catch {
			copied = '';
			copyMessage = $t('shares.card.copyFailed');
			return;
		}
		copied = text;
		copyMessage = '';
		toast.success($t('shares.card.copied'));
		copiedTimer = setTimeout(() => {
			copied = '';
		}, 1500);
	}
</script>

<!-- "alice, bob": the users who get access; one that does not count yet is muted with a short tag,
     the reason as hover text and as text for screen readers (not struck through). -->
{#snippet names(
	entries: (ShareAccess | ShareGroupMember)[]
)}{#each entries as entry, i}{#if i > 0}{', '}{/if}{#if entry.effective}{entry.username}{:else}<span
				class="share-user-inactive"
				data-inactive
				title={reason(entry)}
				>{entry.username}{#if tag(entry)}<span class="ml-1" aria-hidden="true"
						><Badge variant="neutral">{tag(entry)}</Badge></span
					>{/if}</span
			><span class="sr-only">{` ${$t('shares.card.notActive', { reason: reason(entry) })}`}</span>{/if}{/each}{/snippet}

<!-- An address to connect with, and its copy button. -->
{#snippet address(text: string, label: string)}
	<span class="inline-flex max-w-full items-center gap-1">
		<span class="mono-xs break-all">{text}</span>
		<Button variant="ghost" size="icon-sm" class="shrink-0" title={label} onclick={() => copy(text)}>
			{#if copied === text}<Check class="h-3.5 w-3.5" />{:else}<Copy class="h-3.5 w-3.5" />{/if}
			<span class="sr-only">{label}</span>
		</Button>
	</span>
{/snippet}

<article class="card space-y-3" data-share={share.name} aria-labelledby="share-{share.name}-title">
	<div class="share-head">
		<div class="min-w-0 space-y-1">
			<div class="flex flex-wrap items-center gap-2" data-share-badges>
				<Checkbox
					checked={selected}
					disabled={selectDisabled}
					ariaLabel={$t('shares.card.select', { name: share.name })}
					onchange={(on) => onselect?.({ share, selected: on })}
				/>
				<h2 class="card-title break-all" id="share-{share.name}-title">{share.name}</h2>
				<!-- States first, in their colours; then the features, neutral. -->
				{#if !share.served}
					<Badge variant="error">{$t('shares.card.notServed')}</Badge>
				{/if}
				{#if share.pool_mounted === false}
					<Badge variant="error">{$t('shares.card.poolNotMounted')}</Badge>
				{/if}
				{#if share.served && !share.available}
					<Badge variant="warning">{$t('shares.card.noAccess')}</Badge>
				{/if}
				{#if share.permissions_ok === false}
					<Badge variant="warning">{$t('shares.card.permissions')}</Badge>
				{/if}
				{#if repairing}
					<Badge variant="info">{$t('shares.card.repairing')}</Badge>
				{/if}
				{#each features as feature (feature)}
					<Badge variant="outline">{feature}</Badge>
				{/each}
			</div>
			<p class="mono-xs text-muted break-all">{share.path}</p>
			{#if share.comment}
				<p class="text-sm text-secondary">{share.comment}</p>
			{/if}
		</div>
		<div class="share-actions">
			<Button variant="secondary" size="sm" icon={Edit} onclick={() => onedit?.(share)}>
				{$t('shares.card.edit')}<span class="sr-only">{` ${$t('shares.card.editOf', { name: share.name })}`}</span>
			</Button>
			{#if share.served}
				<Button variant="secondary" size="sm" icon={Wrench} onclick={() => onrepair?.(share)}>
					{$t('shares.card.repair')}<span class="sr-only">{` ${$t('shares.card.repairOf', { name: share.name })}`}</span
					>
				</Button>
			{/if}
			<Button
				variant="ghost-danger"
				size="icon-sm"
				title={$t('shares.card.deleteShort')}
				onclick={() => ondelete?.(share)}
			>
				<Trash2 class="w-4 h-4" />
				<span class="sr-only">{$t('shares.card.delete', { name: share.name })}</span>
			</Button>
		</div>
	</div>

	{#if !share.served && share.path_problem}
		<p class="text-sm text-error break-words">{share.path_problem}</p>
	{/if}
	{#each offSettings as item (item.setting)}
		<!-- One line, and the server's reason under Details. -->
		<div data-share-off>
			<HelpText text={item.text} class="text-status-warning">
				{#snippet more()}<p class="break-words text-content-secondary">{item.reason}</p>{/snippet}
			</HelpText>
		</div>
	{/each}

	<div class="text-sm space-y-1">
		{#if groups.length === 0 && groupAccess.length === 0}
			<p class="text-muted">
				{share.guest_ok ? $t('shares.card.nobodyOwn') : $t('shares.card.nobody')}
			</p>
		{:else}
			<!-- "Read & write: alice, bob · Read: carol", muted as in names. -->
			{#if groups.length > 0}<p>
					{#each groups as group, g}{#if g > 0}<span class="text-muted">{' · '}</span>{/if}<span class="text-secondary"
							>{group.label}:</span
						>{' '}{@render names(group.entries)}{/each}
				</p>{/if}
			<!-- "Group family (Read & write): bob, carol", muted as in names. -->
			{#each groupAccess as entry (entry.group_id)}
				<p>
					<span class="text-secondary"
						>{$t('shares.card.group', {
							group: entry.group,
							level: entry.level === 'write' ? $t('shares.level.write') : $t('shares.card.read')
						})}</span
					>{' '}{#if entry.members.length === 0}<span class="text-muted">{$t('shares.card.groupEmpty')}</span
						>{:else}{@render names(entry.members)}{/if}
				</p>
			{/each}
			{#each raised as entry (entry.username)}
				<p class="text-xs text-muted">{$t('shares.card.raised', { username: entry.username })}</p>
			{/each}
		{/if}
		{#if share.served}
			<div class="flex flex-wrap items-center gap-x-2 text-secondary" data-connect>
				<span>{$t('shares.card.connect')}</span>
				{@render address(windowsAddress(host, share.name), $t('shares.card.copyWindows'))}
				{@render address(smbUrl(host, share.name), $t('shares.card.copyUrl'))}
				{#if copyMessage}<span class="w-full text-xs" role="status">{copyMessage}</span>{/if}
			</div>
		{/if}
	</div>

	{#if share.import_notes.length > 0}
		<Alert variant="info" size="sm" title={$t('shares.card.importNotes')}>
			<ul class="list-disc ml-5 space-y-1 text-sm">
				{#each share.import_notes as note}
					<li>{note}</li>
				{/each}
			</ul>
			<div class="mt-2">
				<Button variant="secondary" size="sm" loading={dismissing} onclick={() => ondismissnotes?.(share)}>
					{$t('shares.card.dismiss')}<span class="sr-only"
						>{` ${$t('shares.card.dismissOf', { name: share.name })}`}</span
					>
				</Button>
			</div>
		</Alert>
	{/if}
</article>

<style>
	/* The utilities and tokens of app.css for @apply (Tailwind CSS 4, ADR-0450). */
	@reference '../../../app.css';

	/* The name and its facts on the left, the actions at the top right; on a phone the actions go
	   under the facts, in one row, the same for every share. */
	.share-head {
		@apply grid grid-cols-1 items-start gap-3;
	}

	@media (min-width: 640px) {
		.share-head {
			grid-template-columns: minmax(0, 1fr) auto;
		}
	}

	.share-actions {
		@apply flex flex-wrap items-center gap-2 sm:flex-nowrap sm:justify-end;
	}

	.share-user-inactive {
		@apply text-content-muted;
	}
</style>
