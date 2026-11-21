<script lang="ts">
	import { Network, Trash2 } from 'lucide-svelte';
	import type { NfsExport, NfsExportClient } from '$lib/api/shares';
	import { Badge, Button, Card, EmptyState, ErrorState, HelpText } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	type Props = {
		/** The NFS exports of the host: those that UNOS added and those of others. */
		exports: NfsExport[];
		/** Why the exports could not be read. */
		error: string;
		/** No pool to export a folder of: the empty state leads to Storage. */
		noPool?: boolean;
		/** The delete button of an export that UNOS added: the page asks first. */
		ondelete: (exp: NfsExport) => void;
		/** Read the exports again after a failed load. */
		onretry: () => Promise<void>;
	};

	let { exports, error, noPool = false, ondelete, onretry }: Props = $props();

	let retrying = $state(false);

	async function retry() {
		retrying = true;
		try {
			await onretry();
		} finally {
			retrying = false;
		}
	}

	/** What a client may do, in the words of the SMB access levels. */
	function accessLabel(client: NfsExportClient): string {
		if (client.access === 'write') return $t('shares.level.write');
		if (client.access === 'read') return $t('shares.level.read');
		return $t('shares.nfs.perFlavor');
	}
</script>

<!-- The exports, each a card: its folder, then each client network with its access; the raw
     options of exportfs under Options. No card around them repeats the tab. -->
<section class="space-y-4" aria-label={$t('shares.nfs.title')}>
	{#if error}
		<Card>
			<ErrorState title={$t('shares.nfs.loadError')} message={error} onretry={retry} {retrying} />
		</Card>
	{:else if exports.length === 0}
		<!-- Its next step is the header's Add NFS export, which it names; without a pool, Storage. -->
		<Card>
			<EmptyState icon={Network} title={$t('shares.nfs.empty')} description={$t('shares.nfs.emptyHint')}>
				{#if noPool}
					<a class="link" href="/storage">{$t('shares.toStorage')}</a>
				{/if}
			</EmptyState>
		</Card>
	{:else}
		<ul class="space-y-4" aria-label={$t('shares.nfs.title')}>
			<!-- Not keyed: two exports can start on the same line of a file, and nothing
			     else of an entry is unique. -->
			{#each exports as exp}
				<li class="card space-y-3">
					<div class="nfs-head">
						<div class="min-w-0 space-y-1">
							<p class="mono break-all font-medium text-content">{exp.path}</p>
							{#if !exp.managed}
								<p class="flex flex-wrap items-center gap-2 text-xs text-muted">
									<Badge variant="outline">{$t('shares.nfs.notUnos')}</Badge>
									<span class="break-words">{$t('shares.nfs.notAddedInUnos', { file: exp.file, line: exp.line })}</span>
								</p>
							{/if}
						</div>
						{#if exp.managed}
							<Button variant="ghost-danger" size="icon-sm" title={$t('shares.nfs.delete')} onclick={() => ondelete(exp)}>
								<Trash2 class="w-4 h-4" />
								<span class="sr-only">{$t('shares.nfs.deleteOne', { path: exp.path })}</span>
							</Button>
						{/if}
					</div>
					<ul class="nfs-clients" aria-label={$t('shares.nfs.clientsOf', { path: exp.path })}>
						{#each exp.clients as client}
							<li class="nfs-client">
								<span class="mono break-all">{client.client}</span>
								<!-- " · " for the list's text; the level sits at the right. -->
								<span class="sr-only">{' · '}</span><span class="text-content-secondary">{accessLabel(client)}</span>
							</li>
						{/each}
					</ul>
					{#if exp.clients.some((client) => client.options.length > 0)}
						<!-- design-ignore: each client's export options as exportfs takes them, a list that nfs-exports.spec.ts reads in this details -->
						<details class="text-sm">
							<summary class="cursor-pointer text-content-secondary">{$t('shares.nfs.options')}</summary>
							<ul class="mt-1 space-y-0.5">
								{#each exp.clients as client}
									{#if client.options.length > 0}
										<li class="mono-xs break-all text-content-muted">{client.client}: {client.options.join(',')}</li>
									{/if}
								{/each}
							</ul>
						</details>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}

	<HelpText text={$t('shares.nfs.note')} />
</section>

<style>
	.nfs-head {
		@apply flex items-start justify-between gap-3;
	}

	.nfs-clients {
		@apply divide-y divide-border rounded border border-border text-sm;
	}

	.nfs-client {
		@apply flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 px-3 py-2;
	}
</style>
