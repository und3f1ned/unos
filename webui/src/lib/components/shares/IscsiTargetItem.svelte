<script lang="ts">
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
			<Button variant="secondary" size="sm" onclick={() => onedit(target)}>
				{$t('iscsi.change')}
			</Button>
			<Button variant="danger" size="sm" onclick={() => ondelete(target)}>
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
