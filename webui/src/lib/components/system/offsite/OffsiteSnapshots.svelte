<script lang="ts">
	// The snapshots of the off-device copy (ADR-0337), with their restores: a configuration lands in
	// the backup list, files go into a new folder of a pool, by the form of the snapshot `filesOf`.
	import type { OffsiteSnapshot, OffsiteStatus } from '$lib/api/offsite';
	import { Badge, Button, Input, HelpText, EmptyState, ErrorState } from '$lib/components/ui';
	import { formatSize } from '$lib/format';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';
	import { RotateCcw, X, Camera } from 'lucide-svelte';

	type Props = {
		snapshots: OffsiteSnapshot[];
		// Why the snapshots could not be listed: the server's message.
		snapshotsError: string;
		running: OffsiteStatus['running'];
		// The snapshot being restored.
		restoring: string | null;
		// The snapshot whose Restore files form is open, the pools it offers, and its fields.
		filesOf: string | null;
		restorePools: string[];
		restorePool: string;
		restorePath: string;
		onrestoreconfiguration: (snapshot: OffsiteSnapshot) => void;
		onopenfiles: (snapshot: OffsiteSnapshot) => void;
		onrestorefiles: (snapshot: OffsiteSnapshot) => void;
		oncancelfiles: () => void;
	};

	let {
		snapshots,
		snapshotsError,
		running,
		restoring,
		filesOf,
		restorePools,
		restorePool = $bindable(),
		restorePath = $bindable(),
		onrestoreconfiguration,
		onopenfiles,
		onrestorefiles,
		oncancelfiles
	}: Props = $props();

	// The kind of a snapshot, by its tags, with the keys of their texts (ADR-0287).
	const SNAPSHOT_KINDS: Record<string, string> = {
		configuration: 'offsite.snapshots.configuration',
		data: 'offsite.snapshots.data',
		other: 'offsite.snapshots.other'
	};

	function kindText(snapshotKind: string): string {
		return Object.prototype.hasOwnProperty.call(SNAPSHOT_KINDS, snapshotKind)
			? $t(SNAPSHOT_KINDS[snapshotKind])
			: snapshotKind;
	}
</script>

<h4 class="font-medium text-sm mb-2">{$t('offsite.snapshots.title')}</h4>
{#if snapshotsError}
	<ErrorState title={$t('offsite.snapshots.loadFailed')} message={snapshotsError} class="mb-4" />
{:else if snapshots.length === 0}
	<EmptyState compact icon={Camera} title={$t('offsite.snapshots.empty')} class="mb-4" />
{:else}
	<ul class="space-y-2 mb-4" aria-label={$t('offsite.snapshots.label')}>
		{#each snapshots as snapshot (snapshot.id)}
			<li class="card-nested p-3 text-sm">
				<div class="flex flex-wrap items-center justify-between gap-2">
					<div class="min-w-0 flex flex-wrap items-center gap-x-2 gap-y-1">
						<Badge variant={snapshot.kind === 'configuration' ? 'info' : 'neutral'}>{kindText(snapshot.kind)}</Badge>
						<span>{formatDateTime(snapshot.time, $intlLocale)}</span>
						<span class="font-mono text-xs text-content-muted">{snapshot.short_id}</span>
						{#if snapshot.size !== null}
							<span class="text-content-muted">{formatSize(snapshot.size, $intlLocale, $t, { digits: 2 })}</span>
						{/if}
						{#if snapshot.files !== null && snapshot.kind !== 'configuration'}
							<span class="text-content-muted">{$t('offsite.snapshots.files', { n: snapshot.files })}</span>
						{/if}
					</div>
					<div class="flex flex-wrap gap-2">
						{#if snapshot.kind === 'configuration'}
							<Button
								size="sm"
								variant="secondary"
								icon={RotateCcw}
								loading={restoring === snapshot.id}
								disabled={restoring !== null || running !== null}
								onclick={() => onrestoreconfiguration(snapshot)}
							>
								{$t('offsite.snapshots.restoreConfiguration')}
							</Button>
						{:else}
							<Button
								size="sm"
								variant="secondary"
								icon={RotateCcw}
								disabled={restoring !== null || running !== null}
								onclick={() => onopenfiles(snapshot)}
							>
								{$t('offsite.snapshots.restoreFiles')}
							</Button>
						{/if}
					</div>
				</div>
				<p class="mt-1 font-mono text-xs text-content-secondary break-all">{snapshot.paths.join(', ')}</p>
				{#if filesOf === snapshot.id}
					<form
						class="mt-3 space-y-3"
						onsubmit={(e) => {
							e.preventDefault();
							onrestorefiles(snapshot);
						}}
					>
						<div>
							<label for="offsite-restore-pool-{snapshot.short_id}" class="form-label"
								>{$t('offsite.snapshots.pool')}</label
							>
							<select id="offsite-restore-pool-{snapshot.short_id}" class="form-input" bind:value={restorePool}>
								{#each restorePools as pool}
									<option value={pool}>{pool}</option>
								{/each}
							</select>
						</div>
						<Input
							id="offsite-restore-path-{snapshot.short_id}"
							label={$t('offsite.snapshots.folder')}
							bind:value={restorePath}
							hint={$t('offsite.snapshots.folderHint', { path: snapshot.paths[0] ?? '/' })}
						/>
						<HelpText text={$t('offsite.snapshots.into')} class="mt-1.5" />
						<div class="flex flex-wrap gap-2">
							<Button
								type="submit"
								size="sm"
								loading={restoring === snapshot.id}
								disabled={!restorePool || restoring !== null}
							>
								{$t('offsite.snapshots.restore')}
							</Button>
							<Button size="sm" variant="secondary" icon={X} onclick={oncancelfiles}>
								{$t('common.cancel')}
							</Button>
						</div>
					</form>
				{/if}
			</li>
		{/each}
	</ul>
{/if}
