<script lang="ts">
	import { Network, Trash2 } from 'lucide-svelte';
	import type { NfsExport, NfsExportClient } from '$lib/api/shares';
	import { Alert, Button, Card, EmptyState } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	type Props = {
		/** The NFS exports of the host: those that UNOS added and those of others. */
		exports: NfsExport[];
		/** Why the exports could not be read. */
		error: string;
		/** The delete button of an export that UNOS added: the page asks first. */
		ondelete: (exp: NfsExport) => void;
	};

	let { exports, error, ondelete }: Props = $props();

	/** What a client may do, in the words of the SMB access levels. */
	function accessLabel(client: NfsExportClient): string {
		if (client.access === 'write') return $t('shares.level.write');
		if (client.access === 'read') return $t('shares.level.read');
		return $t('shares.nfs.perFlavor');
	}
</script>

<Card>
	<h2 class="section-title mb-4">
		<Network class="w-5 h-5 text-content-secondary" />
		{$t('shares.nfs.title')}
	</h2>

	{#if error}
		<Alert variant="error" title={$t('shares.nfs.loadError')}>{error}</Alert>
	{:else if exports.length === 0}
		<EmptyState icon={Network} title={$t('shares.nfs.empty')} description={$t('shares.nfs.emptyHint')} />
	{:else}
		<ul class="space-y-2" aria-label={$t('shares.nfs.title')}>
			<!-- Not keyed: two exports can start on the same line of a file, and nothing
			     else of an entry is unique. -->
			{#each exports as exp}
				<li class="card-nested flex items-start justify-between gap-2">
					<div class="min-w-0 space-y-1">
						<p class="mono break-all">{exp.path}</p>
						<ul class="space-y-0.5" aria-label={$t('shares.nfs.clientsOf', { path: exp.path })}>
							{#each exp.clients as client}
								<li class="text-sm">
									<span class="mono break-all">{client.client}</span>
									<span class="text-secondary">· {accessLabel(client)}</span>
									{#if client.options.length > 0}
										<span class="mono-xs text-muted break-all">({client.options.join(',')})</span>
									{/if}
								</li>
							{/each}
						</ul>
						{#if !exp.managed}
							<p class="text-xs text-muted">
								{$t('shares.nfs.notAddedInUnos', { file: exp.file, line: exp.line })}
							</p>
						{/if}
					</div>
					{#if exp.managed}
						<Button variant="ghost-danger" size="icon-sm" onclick={() => ondelete(exp)}>
							<Trash2 class="w-4 h-4" />
							<span class="sr-only">{$t('shares.nfs.deleteOne', { path: exp.path })}</span>
						</Button>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}

	<p class="form-hint mt-4">{$t('shares.nfs.note')}</p>
</Card>
