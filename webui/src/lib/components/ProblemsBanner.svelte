<script lang="ts">
	import { AlertCircle, AlertTriangle } from 'lucide-svelte';
	import { Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { problems, type Problem } from '$lib/stores/problems';

	// The health banner (ADR-0332): the problems of the NAS that are still there, as the events
	// socket sends them to administrators. It goes once the list is empty.

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
		shutdown_idle: 'problems.kind.shutdown_idle'
	};

	// Reads $t where the markup calls it: the text follows the language.
	function text(problem: Problem): string {
		return $t(Object.prototype.hasOwnProperty.call(KINDS, problem.kind) ? KINDS[problem.kind] : 'problems.kind.other', {
			subject: problem.subject
		});
	}
	const anyError = $derived($problems.some((problem) => problem.severity === 'error'));

	// Three problems, the rest behind "Show all N" (ADR-0428): a NAS with many problems keeps the
	// page in view.
	const SHOWN = 3;
	let expanded = $state(false);
	const shown = $derived(expanded ? $problems : $problems.slice(0, SHOWN));
</script>

{#if $problems.length}
	<!-- An alert's look (ADR-0428) without its class, which the specs count on the pages. -->
	<section
		class="mb-6 rounded-lg border border-l-[3px] px-3.5 py-3 {anyError
			? 'border-status-error/35 border-l-status-error bg-status-error/10'
			: 'border-status-warning/35 border-l-status-warning bg-status-warning/10'}"
		aria-labelledby="problems-banner-title"
	>
		<p id="problems-banner-title" class="font-semibold text-content">
			{$t('problems.title', { n: $problems.length })}
		</p>
		<ul id="problems-banner-list" class="mt-2 space-y-1">
			{#each shown as problem (problem.key)}
				<li class="flex items-start gap-2 text-sm text-content">
					{#if problem.severity === 'error'}
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
					     screen (ADR-0302). -->
					<span class="min-w-0 break-words">{text(problem)}</span>
				</li>
			{/each}
		</ul>
		{#if $problems.length > SHOWN}
			<Button
				variant="ghost"
				size="sm"
				class="mt-2 -ml-2"
				aria-expanded={expanded}
				aria-controls="problems-banner-list"
				onclick={() => (expanded = !expanded)}
			>
				{expanded ? $t('problems.showFewer') : $t('problems.showAll', { n: $problems.length })}
			</Button>
		{/if}
	</section>
{/if}
