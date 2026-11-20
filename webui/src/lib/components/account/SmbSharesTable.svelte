<script lang="ts">
	import { FolderOpen } from 'lucide-svelte';
	import type { MySmbStatus } from '$lib/api/auth';
	import { Badge, EmptyState } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	// The SMB shares that the signed-in account may open: each with its access and comment, and
	// whether a guest may open it or the account cannot use it yet.

	type Props = {
		shares: MySmbStatus['shares'];
	};

	let { shares }: Props = $props();
</script>

<div>
	<h4 class="card-title mb-2">{$t('account.smb.shares')}</h4>
	{#if shares.length === 0}
		<EmptyState icon={FolderOpen} title={$t('account.smb.noShares')} />
	{:else}
		<div class="table-frame">
			<table class="table">
				<thead class="table-header">
					<tr>
						<th>{$t('account.smb.name')}</th>
						<th>{$t('account.smb.access')}</th>
						<th>{$t('account.smb.comment')}</th>
					</tr>
				</thead>
				<tbody>
					{#each shares as share (share.name)}
						<tr class="table-row align-top">
							<td>
								<span class="mono">{share.name}</span>
								{#if share.guest_ok}
									<Badge variant="neutral" class="ml-1">{$t('account.smb.guest')}</Badge>
								{/if}
								{#if !share.usable}
									<p class="text-xs text-muted">{$t('account.smb.notUsable')}</p>
								{/if}
							</td>
							<td>{share.level === 'write' ? $t('account.smb.readWrite') : $t('account.smb.readOnly')}</td>
							<td class="text-secondary">{share.comment}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>
