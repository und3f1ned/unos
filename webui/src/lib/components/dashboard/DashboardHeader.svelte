<script lang="ts">
	import { AlertTriangle, Check, RefreshCw, SlidersHorizontal } from 'lucide-svelte';
	import type { SystemInfo } from '$lib/api/system';
	import { Badge, Button, Skeleton, StatusDot } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import { isAdmin } from '$lib/stores/auth';
	import { problemsKnown } from '$lib/stores/problems';
	import { wsConnected, wsForbidden, wsSettled } from '$lib/stores/stats';
	import { apiTime } from '$lib/time';

	// The head of the dashboard: the host's name, system and uptime, a muted line that says whether
	// the live stats come (the time of the last one on hover, so that nothing ticks), for
	// an administrator a chip with the number of issues that the banner below lists, linked to it, or
	// "No problems" once that is known, and the buttons Customize (ADR-0395) and Refresh (ADR-0428).
	// On a narrow screen the line and buttons go below the host name, and a long host name breaks
	// (ADR-0302). Nothing here is a live region.

	type Props = {
		systemInfo: SystemInfo | null;
		/** When the last message of the live stats came. */
		lastUpdate: Date | null;
		/** Whether the layout of the widgets can be changed: it was read (or is being read). */
		customizable: boolean;
		/** The page's first load: the host's name and system are not known yet (ADR-0430). */
		pending: boolean;
		/** The widgets are being arranged. */
		arranging: boolean;
		/**
		 * An administrator's issues, as the banner under the header lists them: the socket's problems
		 * and the dashboard's own; null until the page has loaded, and for a regular user.
		 */
		issueCount?: number | null;
		/** One of them is an error: the chip is red, else amber. */
		issueError?: boolean;
		loading: boolean;
		saving: boolean;
		/** Customize or Done was pressed. */
		oncustomize: () => void;
		onrefresh: () => void;
	};

	let {
		systemInfo,
		lastUpdate,
		customizable,
		pending,
		arranging,
		issueCount = null,
		issueError = false,
		loading,
		saving,
		oncustomize,
		onrefresh
	}: Props = $props();

	// The socket is up: the live stats come.
	const live = $derived(!$wsForbidden && $wsSettled && $wsConnected);


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
		{#if pending}
			<!-- The host's name and system, as tall as they will be (ADR-0430). -->
			<Skeleton size="title" class="w-56 max-w-full" />
			<Skeleton class="mt-0.5 w-72 max-w-full" />
		{:else}
			<h1 class="page-title">{systemInfo?.hostname || $t('common.product')}</h1>
		{/if}
		{#if systemInfo && !pending}
			<!-- A summary of System > General, which an administrator opens from it (ADR-0431). -->
			<p class="page-subtitle">
				{#if $isAdmin}
					<a href="/system#general" class="hover:underline">
						{$t('dashboard.osUptime', { os: systemInfo.os, uptime: formatUptime(systemInfo.boot_time) })}
					</a>
				{:else}
					{$t('dashboard.osUptime', { os: systemInfo.os, uptime: formatUptime(systemInfo.boot_time) })}
				{/if}
			</p>
		{/if}
	</div>
	<div class="page-actions">
		<!-- Whether the live stats come: a muted line, not a chip. -->
		{#if $wsForbidden}
			<span class="flex items-center gap-1.5 text-xs text-status-warning">
				<AlertTriangle class="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
				<span>{$t('dashboard.liveStatsForbidden')}</span>
			</span>
		{:else if !$wsSettled}
			<!-- The first attempt of the live connection has not ended: connecting, not disconnected
			     (ADR-0430). -->
			<span class="flex items-center gap-1.5 text-xs text-content-secondary">
				<StatusDot status="neutral" />
				<span>{$t('dashboard.connecting')}</span>
			</span>
		{:else if !$wsConnected}
			<span class="flex items-center gap-1.5 text-xs text-status-warning">
				<AlertTriangle class="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
				<span>{$t('dashboard.disconnected')}</span>
			</span>
		{:else if live}
			<span
				class="flex items-center gap-1.5 text-xs text-content-secondary"
				title={lastUpdate ? $t('dashboard.updated', { time: formatTime(lastUpdate) }) : undefined}
			>
				<StatusDot status="success" />
				<span>{$t('dashboard.live')}</span>
			</span>
		{/if}
		{#if $isAdmin && issueCount !== null && issueCount > 0}
			<!-- The banner under the header lists them, each linked to where it is dealt with. -->
			<a href="#problems-banner" class="rounded-full">
				<Badge variant={issueError ? 'error' : 'warning'} dot>{$t('dashboard.issues', { n: issueCount })}</Badge>
			</a>
		{:else if $isAdmin && $wsConnected && $problemsKnown && issueCount === 0}
			<Badge variant="success" dot>{$t('dashboard.noProblems')}</Badge>
		{/if}
		{#if customizable}
			<span data-customize>
				<Button
					variant="secondary"
					size="sm"
					icon={arranging ? Check : SlidersHorizontal}
					pressed={arranging}
					disabled={loading}
					onclick={oncustomize}
				>
					{arranging ? $t('dashboard.layout.done') : $t('dashboard.layout.customize')}
				</Button>
			</span>
		{/if}
		<Button
			variant="secondary"
			size="icon-sm"
			icon={RefreshCw}
			loading={loading}
			disabled={saving}
			onclick={onrefresh}
			title={$t('common.refresh')}
		>
			<span class="sr-only">{$t('common.refresh')}</span>
		</Button>
	</div>
</div>
