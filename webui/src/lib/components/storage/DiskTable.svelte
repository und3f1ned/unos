<script lang="ts">
	// The Disks card of the Storage page: the host's whole disks, what they hold and their state, and
	// the button of each disk's idle timeout (ADR-0378).
	import { HardDrive, Timer } from 'lucide-svelte';
	import { Alert, Badge, Button, Card, EmptyState } from '$lib/components/ui';
	import type { Disk, PowerState } from '$lib/api/storage';
	import { t } from '$lib/i18n';
	import type { BadgeVariant } from './pools';

	type Props = {
		disks: Disk[];
		/** Why the disk list could not be loaded. */
		error: string;
		/** The server's warning after a disk's idle timeout was saved (ADR-0378). */
		warning: string;
		formatBytes: (bytes: number) => string;
		/** A label or a name shown on its own, in the quotes of the page's language. */
		quoted: (name: string) => string;
		/** What a disk holds, in words. */
		describeContents: (disk: Disk) => string;
		/** An idle timeout in minutes, in words. */
		timeoutLabel: (minutes: number) => string;
		/** The idle timeout of the disk is to be set. */
		onspindown: (disk: Disk) => void;
		ondismisswarning: () => void;
	};

	let {
		disks,
		error,
		warning,
		formatBytes,
		quoted,
		describeContents,
		timeoutLabel,
		onspindown,
		ondismisswarning
	}: Props = $props();

	const diskStatus = (disk: Disk): { variant: BadgeVariant; label: string } => {
		if (disk.is_system) return { variant: 'error', label: $t('storage.disks.status.system') };
		if (disk.in_use) return { variant: 'warning', label: $t('storage.disks.status.inUse') };
		if (disk.pool) return { variant: 'info', label: $t('storage.disks.status.pool', { pool: disk.pool }) };
		if (disk.has_data) return { variant: 'orange', label: $t('storage.disks.status.hasData') };
		return { variant: 'success', label: $t('storage.disks.status.free') };
	};

	const POWER_STATES: Record<PowerState, { variant: BadgeVariant; key: string }> = {
		active: { variant: 'success', key: 'storage.disks.power.active' },
		standby: { variant: 'info', key: 'storage.disks.power.standby' },
		unknown: { variant: 'neutral', key: 'storage.disks.power.unknown' }
	};

	function diskMountpoints(disk: Disk): string {
		if (disk.mountpoints?.length) return disk.mountpoints.join(', ');
		return disk.mountpoint || '-';
	}
</script>

<!-- A flush card: the table reaches its edges (ADR-0428). The columns keep their order. -->
<Card flush>
	<div class="card-header">
		<h2 class="section-title">
			<HardDrive class="w-5 h-5 text-content-secondary" aria-hidden="true" />
			{$t('storage.disks.title')}
		</h2>
	</div>

	{#if warning}
		<div class="px-5 pb-4">
			<Alert
				variant="warning"
				title={$t('storage.spindown.saved')}
				dismissible
				ondismiss={ondismisswarning}
			>
				{warning}
			</Alert>
		</div>
	{/if}
	{#if error}
		<div class="px-5 pb-5">
			<Alert variant="error" title={$t('storage.disks.loadError')}>{error}</Alert>
		</div>
	{:else if disks.length === 0}
		<EmptyState compact icon={HardDrive} title={$t('storage.disks.none')} />
	{:else}
		<div class="table-frame">
			<table class="table">
				<thead class="table-header">
					<tr>
						<th>{$t('storage.disks.column.device')}</th>
						<th>{$t('storage.disks.column.model')}</th>
						<th class="num">{$t('storage.disks.column.size')}</th>
						<th>{$t('storage.disks.column.type')}</th>
						<th>{$t('storage.disks.column.contents')}</th>
						<th>{$t('storage.disks.column.mountedAt')}</th>
						<th>{$t('storage.disks.column.status')}</th>
						<th>{$t('storage.disks.column.power')}</th>
					</tr>
				</thead>
				<tbody>
					{#each disks as disk}
						{@const status = diskStatus(disk)}
						<tr class="table-row align-top">
							<td class="mono whitespace-nowrap">{disk.path}</td>
							<td>
								<p>{disk.model || '-'}</p>
								{#if disk.serial}
									<p class="mono-xs text-muted">{disk.serial}</p>
								{/if}
							</td>
							<td class="num">{formatBytes(disk.size)}</td>
							<td>
								<!-- A type is no state: a neutral badge. -->
								<Badge variant="neutral">
									{$t(disk.rotational ? 'storage.disks.hdd' : 'storage.disks.ssd')}
								</Badge>
							</td>
							<td>
								<p>{describeContents(disk)}</p>
								{#each disk.partitions ?? [] as part}
									<!-- Svelte 5 trims the whitespace between the iterations of an each block: the
									     cell's text keeps a space between the partitions, as Svelte 4 wrote it. -->
									{' '}<p class="mono-xs text-muted">
										{part.name} · {formatBytes(part.size)}{part.fstype
											? ` · ${part.fstype}`
											: ''}{part.label ? ` ${quoted(part.label)}` : ''}
									</p>
								{/each}
							</td>
							<td class="mono-xs">{diskMountpoints(disk)}</td>
							<td>
								<Badge variant={status.variant}>{status.label}</Badge>
								{#if disk.in_use_reason}
									<p class="text-xs text-muted mt-1">{disk.in_use_reason}</p>
								{/if}
							</td>
							<td>
								<!-- The power state and the idle timeout's button on one line. -->
								<div class="flex flex-wrap items-center gap-2">
								<Badge variant={POWER_STATES[disk.power_state].variant}>
									{$t(POWER_STATES[disk.power_state].key)}
								</Badge>
								<div class="relative">
									<Button
										variant="secondary"
										size="sm"
										icon={Timer}
										disabled={disk.spindown_problem !== null}
										onclick={() => onspindown(disk)}
									>
										<span class="sr-only">{$t('storage.spindown.of', { disk: disk.path })}</span>
										{timeoutLabel(disk.spindown)}
									</Button>
								</div>
								</div>
								{#if disk.spindown_problem}
									<p class="text-xs text-muted mt-1">{disk.spindown_problem}</p>
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</Card>
