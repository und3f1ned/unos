<script lang="ts">
	import { AlertTriangle, Check, CheckCircle, RefreshCw, SlidersHorizontal } from 'lucide-svelte';
	import type { SystemInfo } from '$lib/api/system';
	import { Button, StatusDot } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import { isAdmin } from '$lib/stores/auth';
	import { problems, problemsKnown } from '$lib/stores/problems';
	import { wsConnected, wsForbidden } from '$lib/stores/stats';
	import { apiTime } from '$lib/time';

	// The head of the dashboard: the host's name, system and uptime, a chip that says how fresh the
	// live stats are (or why there are none), for an administrator a chip "No problems" while the
	// problems banner has nothing to say, and the buttons Customize (ADR-0395) and Refresh
	// (ADR-0428). On a narrow screen the chips and buttons go below the host name, and a long host
	// name breaks (ADR-0302). The chips are no live region: the time changes every 2 seconds.

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

<div class="page-header">
	<div class="min-w-0">
		<h1 class="page-title">{systemInfo?.hostname || $t('common.product')}</h1>
		{#if systemInfo}
			<p class="page-subtitle">
				{$t('dashboard.osUptime', { os: systemInfo.os, uptime: formatUptime(systemInfo.boot_time) })}
			</p>
		{/if}
	</div>
	<div class="page-actions">
		{#if $wsForbidden}
			<span class="chip text-status-warning">
				<AlertTriangle class="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
				<span>{$t('dashboard.liveStatsForbidden')}</span>
			</span>
		{:else if !$wsConnected}
			<span class="chip text-status-warning">
				<AlertTriangle class="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
				<span>{$t('dashboard.disconnected')}</span>
			</span>
		{:else if lastUpdate}
			<span class="chip">
				<StatusDot status="success" />
				<span>{$t('dashboard.updated', { time: formatTime(lastUpdate) })}</span>
			</span>
		{/if}
		{#if $isAdmin && $wsConnected && $problemsKnown && $problems.length === 0}
			<span class="chip text-status-success">
				<CheckCircle class="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
				<span>{$t('dashboard.noProblems')}</span>
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
		<Button variant="secondary" size="icon-sm" onclick={onrefresh} disabled={loading || saving} title={$t('common.refresh')}>
			<RefreshCw class="w-4 h-4 {loading ? 'animate-spin' : ''}" aria-hidden="true" />
			<span class="sr-only">{$t('common.refresh')}</span>
		</Button>
	</div>
</div>
