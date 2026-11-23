<script lang="ts">
	import { AlertCircle, AlertTriangle, ChevronRight } from 'lucide-svelte';
	import { page } from '$app/state';
	import { Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { problems, type Problem } from '$lib/stores/problems';
	import type { ExtraIssue } from './dashboard/issues';

	// The health banner (ADR-0332): the problems of the NAS that are still there, as the events
	// socket sends them to administrators, each a link to the page where it is dealt with.
	// It goes once the list is empty. The layout draws it above every page but the dashboard, which
	// draws it under its header with what it knows besides (`extra`: the disks to replace soon, the
	// pools whose health the server reports no problem for), so that one list says it all once.

	type Props = {
		/** Drawn by the dashboard, under its header: the layout's banner leaves that page to it. */
		dashboard?: boolean;
		extra?: ExtraIssue[];
	};

	let { dashboard = false, extra = [] }: Props = $props();

	// The text of each kind of problem (supervisor/src/health/problems.py): keys of the catalog.
	const KINDS: Record<string, string> = {
		pool_unmounted: 'problems.kind.pool_unmounted',
		pool_degraded: 'problems.kind.pool_degraded',
		pool_read_only: 'problems.kind.pool_read_only',
		pool_low_space: 'problems.kind.pool_low_space',
		disk_failed: 'problems.kind.disk_failed',
		disk_test_failed: 'problems.kind.disk_test_failed',
		task_failed: 'problems.kind.task_failed',
		task_missed: 'problems.kind.task_missed',
		volume_quota_80: 'problems.kind.volume_quota_80',
		volume_quota_90: 'problems.kind.volume_quota_90',
		volume_quota_95: 'problems.kind.volume_quota_95',
		app_memory: 'problems.kind.app_memory',
		app_cpu: 'problems.kind.app_cpu',
		app_oom: 'problems.kind.app_oom',
		shutdown_scheduled: 'problems.kind.shutdown_scheduled',
		shutdown_postponed: 'problems.kind.shutdown_postponed',
		shutdown_idle: 'problems.kind.shutdown_idle',
		emmc_worn: 'problems.kind.emmc_worn',
		system_disk_errors: 'problems.kind.system_disk_errors',
		system_disk_failed: 'problems.kind.system_disk_failed',
		standby_sync_failed: 'problems.kind.standby_sync_failed',
		standby_copy_old: 'problems.kind.standby_copy_old',
		standby_card_missing: 'problems.kind.standby_card_missing',
		standby_not_in_boot_path: 'problems.kind.standby_not_in_boot_path',
		standby_has_changes: 'problems.kind.standby_has_changes',
		running_on_standby: 'problems.kind.running_on_standby'
	};

	// Where each kind of problem is dealt with: its pool, disk (the system disk's card too), task, volume,
	// container, the power settings or the SD card standby. A kind this web UI does not know links nowhere.
	const PLACES: [RegExp, string][] = [
		[/^(pool_|volume_quota_)/, '/storage'],
		[/^(disk_|emmc_|system_disk_)/, '/system/health'],
		[/^task_/, '/system/scheduler'],
		[/^app_/, '/containers'],
		[/^shutdown_/, '/system/power'],
		[/^(standby_|running_on_standby$)/, '/system/standby']
	];

	const placeOf = (kind: string): string | undefined => PLACES.find(([pattern]) => pattern.test(kind))?.[1];

	// Reads $t where the markup calls it: the text follows the language.
	function text(problem: Problem): string {
		return $t(Object.prototype.hasOwnProperty.call(KINDS, problem.kind) ? KINDS[problem.kind] : 'problems.kind.other', {
			subject: problem.subject
		});
	}

	type Item = ExtraIssue;
	// The socket's problems and the page's, the errors first; each keeps its order.
	const items = $derived.by((): Item[] => {
		const all: Item[] = [
			...$problems.map((problem) => ({
				key: problem.key,
				severity: problem.severity,
				text: text(problem),
				href: placeOf(problem.kind)
			})),
			...extra
		];
		return [...all.filter((item) => item.severity === 'error'), ...all.filter((item) => item.severity !== 'error')];
	});
	const anyError = $derived(items.some((item) => item.severity === 'error'));
	const hidden = $derived(!dashboard && page.url.pathname === '/');

	// Three problems, the rest behind "Show all N" (ADR-0428): a NAS with many problems keeps the
	// page in view.
	const SHOWN = 3;
	let expanded = $state(false);
	const shown = $derived(expanded ? items : items.slice(0, SHOWN));
</script>

{#if items.length && !hidden}
	<!-- An alert's look (ADR-0428) without its class, which the specs count on the pages. -->
	<section
		id="problems-banner"
		class="mb-6 rounded-lg border border-l-[3px] px-3.5 py-3 scroll-mt-4 {anyError
			? 'border-status-error/35 border-l-status-error bg-status-error/10'
			: 'border-status-warning/35 border-l-status-warning bg-status-warning/10'}"
		aria-labelledby="problems-banner-title"
	>
		<p id="problems-banner-title" class="font-semibold text-content">
			{$t('problems.title', { n: items.length })}
		</p>
		<ul id="problems-banner-list" class="mt-2 space-y-1">
			{#each shown as item (item.key)}
				<li class="flex items-start gap-2 text-sm text-content">
					{#if item.severity === 'error'}
						<AlertCircle
							class="w-4 h-4 flex-shrink-0 mt-0.5 text-status-error"
							role="img"
							aria-label={$t('problems.error')}
						/>
					{:else}
						<AlertTriangle
							class="w-4 h-4 flex-shrink-0 mt-0.5 text-status-warning"
							role="img"
							aria-label={$t('problems.warning')}
						/>
					{/if}
					<!-- A long pool name or path breaks: on a phone it would push the banner beyond the
					     screen (ADR-0302). The problem is the link to where it is dealt with. -->
					<span class="min-w-0 break-words">
						{#if item.href}
							<a href={item.href} class="group inline hover:underline underline-offset-2"
								>{item.text}<ChevronRight
									class="inline w-3.5 h-3.5 ml-0.5 -mt-0.5 text-content-secondary group-hover:text-content"
									aria-hidden="true"
								/></a
							>
						{:else}
							{item.text}
						{/if}
						{#if item.detail}
							<span class="block text-xs text-content-secondary">{item.detail}</span>
						{/if}
					</span>
				</li>
			{/each}
		</ul>
		{#if items.length > SHOWN}
			<Button
				variant="ghost"
				size="sm"
				class="mt-2 -ml-2"
				aria-expanded={expanded}
				aria-controls="problems-banner-list"
				onclick={() => (expanded = !expanded)}
			>
				{expanded ? $t('problems.showFewer') : $t('problems.showAll', { n: items.length })}
			</Button>
		{/if}
	</section>
{/if}
