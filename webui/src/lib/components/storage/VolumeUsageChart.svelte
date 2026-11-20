<script lang="ts">
	import type { VolumeUsagePoint } from '$lib/api/storage';
	import { intlLocale, t } from '$lib/i18n';
	import { apiTime } from '$lib/time';

	// A volume's use over time in the Volumes dialog of the Storage page: one point per
	// hour of the last 30 days that the supervisor kept, the current reading last, drawn as a line
	// below the quota's, with its first and last time and use written out for every reader.

	type Props = {
		points: VolumeUsagePoint[];
		/** A size in the units of the page. */
		formatBytes: (bytes: number) => string;
	};

	let { points, formatBytes }: Props = $props();

	const WIDTH = 300;
	const HEIGHT = 80;

	let times = $derived(points.map((point) => apiTime(point.time)?.getTime() ?? NaN));
	let first = $derived(times[0] ?? 0);
	let last = $derived(times[times.length - 1] ?? 0);
	// The top of the chart: the highest quota or use, so that the quota's line is in view.
	let top = $derived(Math.max(1, ...points.map((point) => Math.max(point.used, point.quota ?? 0))));
	const x = (time: number): number => (last > first ? ((time - first) / (last - first)) * WIDTH : 0);
	const y = (bytes: number): number => HEIGHT - (bytes / top) * HEIGHT;

	/** An SVG path through the values, broken where a point has none. */
	function path(values: (number | null)[], at: (index: number) => number, height: (value: number) => number): string {
		let drawn = '';
		let joined = false;
		values.forEach((value, index) => {
			if (value === null) {
				joined = false;
				return;
			}
			// i18n-ignore: the commands of an SVG path
			drawn += (joined ? ' L ' : ' M ') + at(index).toFixed(1) + ' ' + height(value).toFixed(1);
			joined = true;
		});
		return drawn.trim();
	}

	let usedPath = $derived(path(points.map((point) => point.used), (index) => x(times[index]), y));
	let quotaPath = $derived(path(points.map((point) => point.quota), (index) => x(times[index]), y));
	let quota = $derived(points[points.length - 1]?.quota ?? null);
	let dateFormat = $derived(
		new Intl.DateTimeFormat($intlLocale, {
			day: 'numeric',
			month: 'short',
			hour: '2-digit',
			minute: '2-digit'
		})
	);
</script>

{#if points.length < 2}
	<p class="text-xs text-muted">{$t('storage.volumes.chartTooFew')}</p>
{:else}
	<figure class="space-y-1">
		<svg
			viewBox="0 0 {WIDTH} {HEIGHT}"
			preserveAspectRatio="none"
			class="w-full h-20 rounded bg-surface-muted"
			aria-hidden="true"
		>
			{#if quotaPath}
				<path
					d={quotaPath}
					fill="none"
					class="stroke-status-warning"
					stroke-width="1.5"
					stroke-dasharray="4 3"
					vector-effect="non-scaling-stroke"
				/>
			{/if}
			<path d={usedPath} fill="none" class="stroke-status-info" stroke-width="2" vector-effect="non-scaling-stroke" />
		</svg>
		<figcaption class="text-xs text-secondary break-words">
			{$t('storage.volumes.chartSummary', {
				from: dateFormat.format(first),
				to: dateFormat.format(last),
				start: formatBytes(points[0].used),
				end: formatBytes(points[points.length - 1].used)
			})}
			{#if quota !== null}
				{$t('storage.volumes.chartQuota', { quota: formatBytes(quota) })}
			{/if}
		</figcaption>
	</figure>
{/if}
