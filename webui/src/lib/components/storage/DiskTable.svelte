<script lang="ts">
	// The Disks card of the Storage page, under the pools: the host's whole disks, what they hold and
	// their state, and each disk's power: awake or asleep, and its idle timeout, which its button
	// sets (ADR-0378). Where a disk is mounted is its status's reason (ADR-0432).
	import { HardDrive, Pencil } from 'lucide-svelte';
	import { Alert, Badge, Button, Card, EmptyState, StatusDot } from '$lib/components/ui';
	import type { Disk, PowerState } from '$lib/api/storage';
	import { t } from '$lib/i18n';
	import type { BadgeVariant } from './pools';

	type Props = {
		disks: Disk[];
		/** Why the disk list could not be loaded. */
		error: string;
		/** The reason is not the pools card's already: it is shown under the title. */
		errorDetail?: boolean;
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
		errorDetail = true,
		warning,
		formatBytes,
		quoted,
		describeContents,
		timeoutLabel,
		onspindown,
		ondismisswarning
	}: Props = $props();

	const diskStatus = (disk: Disk): { variant: BadgeVariant; label: string } => {
		// The system disk is no fault: it is just not for pools.
		if (disk.is_system) return { variant: 'neutral', label: $t('storage.disks.status.system') };
		if (disk.in_use) return { variant: 'warning', label: $t('storage.disks.status.inUse') };
		if (disk.pool) return { variant: 'info', label: $t('storage.disks.status.pool', { pool: disk.pool }) };
		if (disk.has_data) return { variant: 'orange', label: $t('storage.disks.status.hasData') };
		return { variant: 'success', label: $t('storage.disks.status.free') };
	};

	// A disk's power as a dot and a word: awake, asleep, or not known (ADR-0432).
	const POWER_STATES: Record<PowerState, { dot: 'success' | 'neutral' | null; key: string }> = {
		active: { dot: 'success', key: 'storage.disks.power.active' },
		standby: { dot: 'neutral', key: 'storage.disks.power.standby' },
		unknown: { dot: null, key: 'storage.disks.power.unknown' }
	};
</script>

<!-- A flush card: the table reaches its edges (ADR-0428). -->
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
				size="sm"
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
			<Alert variant="error" size="sm" title={$t('storage.disks.loadError')}>
				{#if errorDetail}
					<span class="text-xs text-content-secondary break-words">{error}</span>
				{/if}
			</Alert>
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
						<th>{$t('storage.disks.column.status')}</th>
						<th>{$t('storage.disks.column.power')}</th>
					</tr>
				</thead>
				<tbody>
					{#each disks as disk}
						{@const status = diskStatus(disk)}
						{@const power = POWER_STATES[disk.power_state]}
						<tr class="table-row align-top">
							<td class="mono whitespace-nowrap">{disk.path}</td>
							<td class="min-w-[9rem]">
								<p>{disk.model || '-'}</p>
								{#if disk.serial}
									<p class="mono-xs text-muted">{disk.serial}</p>
								{/if}
							</td>
							<td class="num whitespace-nowrap">{formatBytes(disk.size)}</td>
							<td>
								<!-- A type is no state: a neutral badge. -->
								<Badge variant="neutral">
									{$t(disk.rotational ? 'storage.disks.hdd' : 'storage.disks.ssd')}
								</Badge>
							</td>
							<td class="min-w-[12rem]">
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
							<td class="min-w-[9rem]">
								<Badge variant={status.variant} class="whitespace-nowrap">{status.label}</Badge>
								{#if disk.in_use_reason}
									<p class="text-xs text-muted mt-1">{disk.in_use_reason}</p>
								{/if}
							</td>
							<td class="min-w-[11rem]">
								<!-- Awake or asleep, then the idle timeout and its button, which says whose it is
								     to screen readers (ADR-0432). A disk that cannot get one says why instead. -->
								<p class="flex items-center gap-1.5 whitespace-nowrap">
									{#if power.dot}<StatusDot status={power.dot} />{/if}
									{$t(power.key)}
								</p>
								{#if disk.spindown_problem}
									<p class="text-xs text-muted mt-1">{disk.spindown_problem}</p>
								{:else}
									<p class="flex flex-wrap items-center gap-x-1 mt-0.5 text-xs text-content-secondary">
										<!-- The button's name says it to screen readers, with the disk. -->
										<span aria-hidden="true">{$t('storage.disks.idleTimeout')}</span>
										<Button
											variant="ghost"
											size="sm"
											class="h-7 px-1.5 -my-1"
											onclick={() => onspindown(disk)}
										>
											<span class="sr-only">{$t('storage.spindown.of', { disk: disk.path })}</span>
											{timeoutLabel(disk.spindown)}
											<Pencil class="w-3.5 h-3.5 text-content-secondary" aria-hidden="true" />
										</Button>
									</p>
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</Card>
