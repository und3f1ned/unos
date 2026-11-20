<script lang="ts">
	// The btrfs filesystems on the host's disks that are not pools yet (ADR-0283): without a label,
	// with one that is no pool name, or with one that another filesystem has too, when the pool of
	// that name is the other one or neither. A pool found on the disks is a pool of the Pools card,
	// and its Mount imports it. The card shows only when there is one, or the search failed.
	import { HardDriveDownload } from 'lucide-svelte';
	import { Alert, Button, Card } from '$lib/components/ui';
	import type { ImportableFilesystem } from '$lib/api/storage';
	import { t } from '$lib/i18n';

	type Props = {
		filesystems: ImportableFilesystem[];
		/** Why the disks could not be searched. */
		error: string;
		formatBytes: (bytes: number) => string;
		/** The filesystem is to be imported as a pool. */
		onimport: (fs: ImportableFilesystem) => void;
	};

	let { filesystems, error, formatBytes, onimport }: Props = $props();

	const describeFilesystem = (fs: ImportableFilesystem): string =>
		fs.label === null ? $t('storage.found.noLabel') : $t('storage.found.label', { label: fs.label });

	/** Why the filesystem is not a pool yet: the API mounts a pool under its label. */
	const labelHint = (fs: ImportableFilesystem): string => {
		if (fs.label_problem === 'duplicate') return $t('storage.found.duplicate', { label: fs.label ?? '' });
		if (fs.label_problem === 'invalid') return $t('storage.found.invalid');
		return $t('storage.found.missing');
	};

	const missingDevicesText = (fs: ImportableFilesystem): string =>
		$t('storage.found.missingDevices', { n: fs.missing_devices, total: fs.total_devices });
</script>

{#if filesystems.length > 0 || error}
	<Card>
		<section aria-labelledby="found-on-the-disks">
			<h2 id="found-on-the-disks" class="section-title mb-4">
				<HardDriveDownload class="w-5 h-5" />
				{$t('storage.found.title')}
			</h2>
			{#if error}
				<Alert variant="error" title={$t('storage.found.searchError')}>{error}</Alert>
			{:else}
				<p class="text-sm text-secondary mb-4">{$t('storage.found.intro')}</p>
				<ul class="space-y-3">
					{#each filesystems as fs (fs.uuid)}
						<li class="card-nested flex flex-wrap items-start justify-between gap-3">
							<div class="min-w-0 space-y-1">
								<p class="font-semibold">{describeFilesystem(fs)}</p>
								<p class="mono-xs text-muted break-all">{$t('storage.found.uuid', { uuid: fs.uuid })}</p>
								<p class="text-sm text-secondary">
									<span class="mono-xs">{fs.devices.join(', ')}</span> · {$t('storage.found.usage', {
										size: formatBytes(fs.size),
										used: formatBytes(fs.used)
									})}
								</p>
								<p class="text-xs text-muted">{labelHint(fs)}</p>
								{#if fs.missing_devices > 0}
									<p class="text-sm text-warning">{missingDevicesText(fs)}</p>
								{/if}
							</div>
							<Button
								size="sm"
								icon={HardDriveDownload}
								disabled={fs.missing_devices > 0}
								onclick={() => onimport(fs)}
							>
								{$t('storage.found.import')}<span class="sr-only"
									> {$t('storage.found.importOne', { uuid: fs.uuid })}</span
								>
							</Button>
						</li>
					{/each}
				</ul>
			{/if}
		</section>
	</Card>
{/if}
