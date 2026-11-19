<script lang="ts">
	import { Check, Clock, RefreshCw, Server, SlidersHorizontal } from 'lucide-svelte';
	import type { SystemInfo } from '$lib/api/system';
	import { Button } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import { wsConnected, wsForbidden } from '$lib/stores/stats';
	import { apiTime } from '$lib/time';

	// The head of the dashboard: the host's name, system and uptime, how fresh the live stats are,
	// and the buttons Customize (ADR-0395) and Refresh. On a narrow screen the live stats' time and
	// Refresh go below the host name, and a long host name breaks (ADR-0302).

	type Props = {
		systemInfo: SystemInfo | null;
		/** When the last message of the live stats came. */
		lastUpdate: Date | null;
		/** Whether the layout of the widgets can be changed: it was read, and nothing loads. */
		customizable: boolean;
		/** The widgets are being arranged. */
		arranging: boolean;
		loading: boolean;
		saving: boolean;
		/** Customize or Done was pressed. */
		oncustomize: () => void;
		onrefresh: () => void;
	};

	let { systemInfo, lastUpdate, customizable, arranging, loading, saving, oncustomize, onrefresh }: Props = $props();

	// The functions that read $t or $intlLocale do it where the markup calls them: the texts follow
	// the language.
	function formatUptime(bootTime: string): string {
		// bootTime is when the host booted, ISO 8601 with its offset (ADR-0401)
		const booted = apiTime(bootTime);
		if (!booted) return bootTime;
		const seconds = Math.floor((Date.now() - booted.getTime()) / 1000);
		const days = Math.floor(seconds / 86400);
		const hours = Math.floor((seconds % 86400) / 3600);
		const minutes = Math.floor((seconds % 3600) / 60);
		if (days > 0) return $t('dashboard.uptime.days', { days, hours });
		if (hours > 0) return $t('dashboard.uptime.hours', { hours, minutes });
		return $t('dashboard.uptime.minutes', { minutes });
	}

	// In the language of the page.
	function formatTime(date: Date): string {
		return date.toLocaleTimeString($intlLocale);
	}
</script>

<div class="flex flex-wrap items-center justify-between gap-3">
	<div class="flex items-center gap-3 min-w-0">
		<Server class="w-8 h-8 text-brand-ring flex-shrink-0" />
		<div class="min-w-0">
			<h1 class="text-2xl font-bold break-words">{systemInfo?.hostname || $t('common.product')}</h1>
			{#if systemInfo}
				<p class="text-sm text-content-secondary">
					{$t('dashboard.osUptime', { os: systemInfo.os, uptime: formatUptime(systemInfo.boot_time) })}
				</p>
			{/if}
		</div>
	</div>
	<div class="flex items-center gap-4">
		{#if $wsForbidden}
			<span class="text-xs text-warning">{$t('dashboard.liveStatsForbidden')}</span>
		{:else if !$wsConnected}
			<span class="text-xs text-warning">{$t('dashboard.disconnected')}</span>
		{:else if lastUpdate}
			<span class="text-xs text-content-muted flex items-center gap-1">
				<Clock class="w-3 h-3" />
				{$t('dashboard.updated', { time: formatTime(lastUpdate) })}
			</span>
		{/if}
		{#if customizable}
			<span data-customize>
				<Button
					variant="secondary"
					size="sm"
					icon={arranging ? Check : SlidersHorizontal}
					pressed={arranging}
					onclick={oncustomize}
				>
					{arranging ? $t('dashboard.layout.done') : $t('dashboard.layout.customize')}
				</Button>
			</span>
		{/if}
		<Button variant="secondary" size="icon" onclick={onrefresh} disabled={loading || saving}>
			<RefreshCw class="w-4 h-4 {loading ? 'animate-spin' : ''}" />
			<span class="sr-only">{$t('common.refresh')}</span>
		</Button>
	</div>
</div>
