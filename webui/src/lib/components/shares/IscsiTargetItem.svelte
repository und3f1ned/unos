<script lang="ts">
	import { Edit, Trash2 } from 'lucide-svelte';
	import type { IscsiTarget } from '$lib/api/iscsi';
	import { Alert, Badge, Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	type Props = {
		target: IscsiTarget;
		// Its buttons: the tab opens the dialog.
		onedit: (target: IscsiTarget) => void;
		ondelete: (target: IscsiTarget) => void;
	};

	let { target, onedit, ondelete }: Props = $props();
</script>

<!-- A target: its state and who is logged in first, then its facts; Edit and the delete
     icon as on an SMB share's card. -->
<li class="card space-y-3" data-target={target.name}>
	<div class="iscsi-head">
		<div class="min-w-0 space-y-1">
			<div class="flex flex-wrap items-center gap-2">
				<h2 class="card-title break-all">{target.name}</h2>
				{#if target.served}
					<Badge variant="success" dot>{$t('iscsi.served')}</Badge>
				{:else}
					<Badge variant="warning" dot>{$t('iscsi.notServed')}</Badge>
				{/if}
			</div>
			<p class="text-sm text-content-secondary">
				{#if target.sessions.length > 0}
					{$t('iscsi.loggedIn', { n: target.sessions.length })}
				{:else}
					{$t('iscsi.noSessions')}
				{/if}
			</p>
		</div>
		<div class="flex items-center gap-2">
			<Button variant="secondary" size="sm" icon={Edit} onclick={() => onedit(target)}>
				{$t('iscsi.editButton')}<span class="sr-only">{` ${$t('iscsi.editOf', { name: target.name })}`}</span>
			</Button>
			<Button variant="ghost-danger" size="icon-sm" title={$t('iscsi.delete')} onclick={() => ondelete(target)}>
				<Trash2 class="h-4 w-4" />
				<span class="sr-only">{$t('iscsi.deleteOf', { name: target.name })}</span>
			</Button>
		</div>
	</div>
	{#if target.problem}
		<Alert variant="warning" size="sm">{target.problem}</Alert>
	{/if}
	<div class="kv">
		<div>
			<span class="kv-label">{$t('iscsi.lun')}</span>
			<span class="kv-value">
				{$t('iscsi.lunValue', { size: target.size_gib, volume: target.volume, pool: target.pool })}
			</span>
		</div>
		{#if target.sessions.length > 0}
			<div>
				<span class="kv-label">{$t('iscsi.sessions')}</span>
				<span class="kv-value mono-xs">
					{#each target.sessions as session}<span class="block break-all">{session}</span>{/each}
				</span>
			</div>
		{/if}
		<div>
			<span class="kv-label">{$t('iscsi.initiators')}</span>
			<span class="kv-value mono-xs">
				{#each target.initiators as initiator}<span class="block break-all">{initiator}</span>{/each}
			</span>
		</div>
		<div>
			<span class="kv-label">{$t('iscsi.chapUser')}</span>
			<span class="kv-value">
				<span class="break-all">{target.chap_user}</span>{#if target.mutual}<span class="text-content-secondary"
						>{` · ${$t('iscsi.mutualOn')}`}</span
					>{/if}
			</span>
		</div>
		<div>
			<span class="kv-label">{$t('iscsi.iqn')}</span>
			<span class="kv-value mono-xs break-all">{target.iqn}</span>
		</div>
		<div>
			<span class="kv-label">{$t('iscsi.file')}</span>
			<span class="kv-value mono-xs break-all">{target.path}</span>
		</div>
	</div>
</li>

<style>
	/* The utilities and tokens of app.css for @apply (Tailwind CSS 4, ADR-0456). */
	@reference '../../../app.css';

	.iscsi-head {
		@apply grid grid-cols-1 items-start gap-3;
	}

	@media (min-width: 640px) {
		.iscsi-head {
			grid-template-columns: minmax(0, 1fr) auto;
		}
	}
</style>
