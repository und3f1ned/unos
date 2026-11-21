<script lang="ts">
	import type { SmartAttribute, SmartInfo } from '$lib/api/health';
	import { Badge, Modal } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import { HEALTH_STATUS, capacityText, healthClass, orUnknown, textOf } from './texts';

	// A disk's SMART data: the data of GET /health/disks, so that opening a disk runs no smartctl.
	type Props = {
		// The disk whose data the dialog shows; null while it is closed.
		disk: SmartInfo | null;
		onclose: () => void;
	};

	let { disk, onclose }: Props = $props();

	type BadgeVariant = 'success' | 'warning' | 'error' | 'neutral';

	// The server's values, shown by the keys of their texts in the catalog (ADR-0287).
	// "critical": the value is at or below the attribute's failure threshold;
	// "warning": within 10 of it (parse_smart_data in supervisor/src/health/router.py).
	const ATTRIBUTE_STATUS: Record<SmartAttribute['status'], { variant: BadgeVariant; key: string }> = {
		ok: { variant: 'success', key: 'health.attribute.ok' },
		warning: { variant: 'warning', key: 'health.attribute.warning' },
		critical: { variant: 'error', key: 'health.attribute.critical' }
	};
	// The numbers of an NVMe disk's SMART/Health Information log, by smartctl's names.
	const NVME_FIELDS: Record<string, string> = {
		critical_warning: 'health.nvme.criticalWarning',
		temperature: 'health.nvme.temperature',
		available_spare: 'health.nvme.availableSpare',
		available_spare_threshold: 'health.nvme.availableSpareThreshold',
		percentage_used: 'health.nvme.percentageUsed',
		data_units_read: 'health.nvme.dataUnitsRead',
		data_units_written: 'health.nvme.dataUnitsWritten',
		host_reads: 'health.nvme.hostReads',
		host_writes: 'health.nvme.hostWrites',
		controller_busy_time: 'health.nvme.controllerBusyTime',
		power_cycles: 'health.nvme.powerCycles',
		power_on_hours: 'health.nvme.powerOnHours',
		unsafe_shutdowns: 'health.nvme.unsafeShutdowns',
		media_errors: 'health.nvme.mediaErrors',
		num_err_log_entries: 'health.nvme.errorLogEntries',
		warning_temp_time: 'health.nvme.warningTempTime',
		critical_comp_time: 'health.nvme.criticalCompTime'
	};

	// The helpers below that give texts are reactive, so that an open dialog follows a change of
	// the language.

	let attributeStatus = $derived((status: string): { variant: BadgeVariant; label: string } => {
		const known = ATTRIBUTE_STATUS[status as SmartAttribute['status']];
		return known ? { variant: known.variant, label: $t(known.key) } : { variant: 'neutral', label: status };
	});

	/** smartctl's "available_spare_threshold" as "Available spare threshold". */
	function nvmeLabel(key: string): string {
		const words = key.replace(/_/g, ' ');
		return words.charAt(0).toUpperCase() + words.slice(1);
	}

	let nvmeField = $derived((key: string): string =>
		Object.prototype.hasOwnProperty.call(NVME_FIELDS, key) ? $t(NVME_FIELDS[key]) : nvmeLabel(key)
	);

	let orNotReported = $derived((value: number | null, format: (value: number) => string = String): string =>
		value === null ? $t('health.notReported') : format(value)
	);

	/** 51775 power-on hours as 5 years, 332 days; 1200 as 50 days, 0 hours. */
	let powerOnTime = $derived((hours: number): string => {
		const days = Math.floor(hours / 24);
		const years = Math.floor(days / 365);
		return years > 0
			? `${$t('health.years', { n: years })}, ${$t('health.days', { n: days % 365 })}`
			: `${$t('health.days', { n: days })}, ${$t('health.hours', { n: hours % 24 })}`;
	});

	let lastSelfTest = $derived((disk: SmartInfo): string => {
		if (!disk.self_test_status && !disk.last_test_result) return $t('health.noSelfTest');
		return [disk.self_test_status, disk.last_test_result]
			.filter((part): part is string => Boolean(part))
			.map((part) => orUnknown($t, part))
			.join(': ');
	});
</script>

<Modal open={disk !== null} title={disk ? `${orUnknown($t, disk.model)} (${disk.device})` : ''} size="xl" {onclose}>
	{#if disk}
		<dl class="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3 text-sm">
			<div>
				<dt class="text-secondary">{$t('health.overall')}</dt>
				<dd class="font-medium {healthClass(disk.health_status)}">{textOf($t, HEALTH_STATUS, disk.health_status)}</dd>
			</div>
			<div>
				<dt class="text-secondary">{$t('health.temperature')}</dt>
				<dd>{orNotReported(disk.temperature, (celsius) => $t('health.celsius', { value: celsius }))}</dd>
			</div>
			<div>
				<dt class="text-secondary">{$t('health.powerOnHours')}</dt>
				<dd>{orNotReported(disk.power_on_hours, (hours) => `${hours} (${powerOnTime(hours)})`)}</dd>
			</div>
			<div>
				<dt class="text-secondary">{$t('health.powerCycles')}</dt>
				<dd>{orNotReported(disk.power_cycle_count)}</dd>
			</div>
			<div>
				<dt class="text-secondary">{$t('health.serial')}</dt>
				<dd class="mono">{orUnknown($t, disk.serial)}</dd>
			</div>
			<div>
				<dt class="text-secondary">{$t('health.firmware')}</dt>
				<dd class="mono">{orUnknown($t, disk.firmware)}</dd>
			</div>
			<div>
				<dt class="text-secondary">{$t('health.capacity')}</dt>
				<dd>{capacityText($t, $intlLocale, disk.capacity)}</dd>
			</div>
			<div>
				<dt class="text-secondary">{$t('health.lastSelfTest')}</dt>
				<dd>{lastSelfTest(disk)}</dd>
			</div>
		</dl>

		{#if disk.attributes.length > 0}
			<div class="table-frame mt-6">
				<table class="table">
					<caption class="sr-only">{$t('health.attributes')}</caption>
					<thead class="table-header">
						<tr>
							<th>{$t('health.attribute.id')}</th>
							<th>{$t('health.attribute.name')}</th>
							<th>{$t('health.attribute.value')}</th>
							<th>{$t('health.attribute.worst')}</th>
							<th>{$t('health.attribute.threshold')}</th>
							<th>{$t('health.attribute.raw')}</th>
							<th>{$t('health.attribute.status')}</th>
						</tr>
					</thead>
					<tbody>
						{#each disk.attributes as attribute}
							{@const status = attributeStatus(attribute.status)}
							<tr class="table-row">
								<td class="mono">{attribute.id}</td>
								<td>{attribute.name}</td>
								<td>{attribute.value}</td>
								<td>{attribute.worst}</td>
								<td>{attribute.thresh}</td>
								<td class="mono">{attribute.raw_value}</td>
								<td><Badge variant={status.variant}>{status.label}</Badge></td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{:else if disk.nvme_health}
			<div class="table-frame mt-6">
				<table class="table">
					<caption class="sr-only">{$t('health.nvmeLog')}</caption>
					<thead class="table-header">
						<tr>
							<th>{$t('health.nvmeField')}</th>
							<th>{$t('health.attribute.value')}</th>
						</tr>
					</thead>
					<tbody>
						{#each Object.entries(disk.nvme_health) as [key, value]}
							<tr class="table-row">
								<td>{nvmeField(key)}</td>
								<td class="mono">{value}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{:else if disk.health_status === 'STANDBY'}
			<p class="text-secondary text-sm mt-6">{$t('health.asleep')}</p>
		{:else}
			<p class="text-secondary text-sm mt-6">{$t('health.noAttributes')}</p>
		{/if}
	{/if}
</Modal>
