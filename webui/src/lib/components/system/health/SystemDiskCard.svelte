<script lang="ts">
	import type { SystemDiskHealth } from '$lib/api/health';
	import { Alert, Badge, Card, HelpText, TechDetails } from '$lib/components/ui';
	import { formatNumber, formatSize } from '$lib/format';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';
	import { lifeTimeText, preEolKey, systemDiskState } from './texts';

	// The System disk card of Disk Health (ADR-0471): the wear and the errors of an eMMC that holds
	// the system, as the server's last reads found them, or why it shows none. It only reports them:
	// UNOS acts on nothing here.
	type Props = {
		/** GET /health/system-disk; null before it answered or when it failed. */
		health: SystemDiskHealth | null;
		/** Why GET /health/system-disk failed; '' when it did not. */
		error: string;
	};

	let { health, error }: Props = $props();

	const emmc = $derived(health?.emmc ?? null);
	const mark = $derived(health && emmc ? systemDiskState(health) : null);

	// A count of errors since UNOS started; null until the server read it.
	function count(value: number | null): string {
		return value === null ? $t('health.systemDisk.notRead') : formatNumber(value, $intlLocale);
	}
</script>

{#snippet badge()}
	{#if mark}
		<Badge variant={mark.variant} dot>{$t(mark.key)}</Badge>
	{/if}
{/snippet}

<Card title={$t('health.systemDisk.title')} status={mark ? badge : undefined}>
	{#if error}
		<Alert variant="error" size="sm" title={$t('health.systemDisk.loadError')}>{error}</Alert>
	{:else if health && emmc}
		<p class="font-medium break-words">{emmc.model ?? $t('common.unknown')}</p>
		<p class="text-sm text-content-secondary">
			<span class="mono">{emmc.device}</span> · {formatSize(emmc.size, $intlLocale, $t)}
		</p>
		<div class="kv mt-4">
			<div>
				<span class="kv-label">{$t('health.systemDisk.lifeA')}</span>
				<span class="kv-value">{lifeTimeText($t, $intlLocale, emmc.life_time_a)}</span>
			</div>
			<div>
				<span class="kv-label">{$t('health.systemDisk.lifeB')}</span>
				<span class="kv-value">{lifeTimeText($t, $intlLocale, emmc.life_time_b)}</span>
			</div>
			<div>
				<span class="kv-label">{$t('health.systemDisk.preEol')}</span>
				<span class="kv-value">{$t(preEolKey(emmc.pre_eol))}</span>
			</div>
			<div>
				<span class="kv-label">{$t('health.systemDisk.wearRead')}</span>
				<span class="kv-value">
					{emmc.wear_source === 'sysfs'
						? $t('health.systemDisk.asOfBoot')
						: emmc.wear_read_at && emmc.wear_source
							? formatDateTime(emmc.wear_read_at, $intlLocale)
							: $t('health.systemDisk.notRead')}
				</span>
			</div>
			<div>
				<span class="kv-label">{$t('health.systemDisk.ioErrors')}</span>
				<span class="kv-value tabular">{count(emmc.io_errors)}</span>
			</div>
			<div>
				<span class="kv-label">{$t('health.systemDisk.ext4Errors')}</span>
				<span class="kv-value tabular">
					{emmc.filesystem === 'ext4' ? count(emmc.ext4_errors) : $t('health.systemDisk.notExt4')}
				</span>
			</div>
			<div>
				<span class="kv-label">{$t('health.systemDisk.root')}</span>
				<span class="kv-value">
					<span class="mono">{emmc.root}</span> · {$t(
						emmc.read_only === null
							? 'health.systemDisk.stateNotRead'
							: emmc.read_only
								? 'health.systemDisk.readOnly'
								: 'health.systemDisk.readWrite',
						{ filesystem: emmc.filesystem }
					)}
				</span>
			</div>
		</div>
		<!-- The server's technical texts (why a read failed) fold into TechDetails, once. -->
		{#if emmc.wear_source === 'sysfs'}
			<Alert variant="info" size="sm" class="mt-4">
				<p>{$t(emmc.mmc_missing ? 'health.systemDisk.noMmc' : 'health.systemDisk.mmcFailed')}</p>
				{#if emmc.wear_error}
					<TechDetails class="mt-2" text={emmc.wear_error} />
				{/if}
			</Alert>
		{:else if emmc.wear_source === 'mmc' && emmc.wear_error}
			<Alert variant="warning" size="sm" class="mt-4">
				<p>{$t('health.systemDisk.wearStale')}</p>
				<TechDetails class="mt-2" text={emmc.wear_error} />
			</Alert>
		{:else if emmc.wear_source === null && emmc.wear_error}
			<Alert variant="warning" size="sm" class="mt-4">
				<p>{$t('health.systemDisk.wearUnreadable')}</p>
				<TechDetails class="mt-2" text={emmc.wear_error} />
			</Alert>
		{/if}
		{#if emmc.check_error}
			<Alert variant="warning" size="sm" class="mt-4">
				<p>{$t('health.systemDisk.checkFailed')}</p>
				<TechDetails class="mt-2" text={emmc.check_error} />
			</Alert>
		{/if}
		<HelpText class="mt-4" text={$t('health.systemDisk.help')} />
	{:else if health}
		<p class="text-sm text-content-secondary">
			{health.reason === 'not_emmc'
				? $t('health.systemDisk.notEmmc', { disk: health.system_disk ?? '' })
				: $t(health.reason === 'no_disk' ? 'health.systemDisk.noDisk' : 'health.systemDisk.unreadable')}
		</p>
		{#if health.detail}
			<TechDetails class="mt-2" text={health.detail} />
		{/if}
	{/if}
</Card>
