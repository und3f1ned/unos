<script lang="ts">
	import type { DiskPrediction } from '$lib/api/health';
	import { Alert } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	// The disks whose SMART trend says replace soon (ADR-0235), above the widgets, always: a warning
	// is not one of them. A failed check is shown, so that a disk to replace is not taken for one
	// without a mark.

	type Props = {
		toReplace: DiskPrediction[];
		/** Why the predictions could not be read; '' when they were. */
		error: string;
	};

	let { toReplace, error }: Props = $props();
</script>

{#if toReplace.length > 0}
	<Alert variant="error" title={$t('dashboard.replace.title')}>
		<ul class="space-y-1 text-sm">
			{#each toReplace as prediction}
				<li>
					<span class="mono">{prediction.device}</span>
					{$t('dashboard.replace.disk', { serial: prediction.serial, reasons: prediction.risk_factors.join('; ') })}
				</li>
			{/each}
		</ul>
		<a href="/system#health" class="link text-sm">{$t('dashboard.replace.diskHealth')}</a>
	</Alert>
{:else if error}
	<Alert variant="warning" title={$t('dashboard.replace.checkFailed')}>
		{error}
	</Alert>
{/if}
