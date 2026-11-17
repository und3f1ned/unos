<script lang="ts">
	import { AlertCircle, AlertTriangle } from 'lucide-svelte';
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
		app_oom: 'problems.kind.app_oom'
	};

	$: text = (problem: Problem): string =>
		$t(Object.prototype.hasOwnProperty.call(KINDS, problem.kind) ? KINDS[problem.kind] : 'problems.kind.other', {
			subject: problem.subject
		});
	$: anyError = $problems.some((problem) => problem.severity === 'error');
</script>

{#if $problems.length}
	<section
		class="mb-4 rounded-lg border px-4 py-3 {anyError
			? 'border-status-error/50 bg-status-error/10'
			: 'border-status-warning/50 bg-status-warning/10'}"
		aria-labelledby="problems-banner-title"
	>
		<p id="problems-banner-title" class="font-medium text-content">
			{$t('problems.title', { n: $problems.length })}
		</p>
		<ul class="mt-2 space-y-1">
			{#each $problems as problem (problem.key)}
				<li
					class="flex items-start gap-2 text-sm {problem.severity === 'error'
						? 'text-status-error'
						: 'text-status-warning'}"
				>
					{#if problem.severity === 'error'}
						<AlertCircle class="w-4 h-4 flex-shrink-0 mt-0.5" role="img" aria-label={$t('problems.error')} />
					{:else}
						<AlertTriangle class="w-4 h-4 flex-shrink-0 mt-0.5" role="img" aria-label={$t('problems.warning')} />
					{/if}
					<!-- A long pool name or path breaks: on a phone it would push the banner beyond the
					     screen (ADR-0302). -->
					<span class="min-w-0 break-words">{text(problem)}</span>
				</li>
			{/each}
		</ul>
	</section>
{/if}
