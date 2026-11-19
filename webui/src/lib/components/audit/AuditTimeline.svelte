<script lang="ts">
	import type { AuditEntry } from '$lib/api/audit';
	import { Button, Card } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import AuditEntryItem from './AuditEntryItem.svelte';
	import { byDay, dayLabel } from './timeline';

	// The entries of the audit log as a timeline, newest first, grouped by the browser's days
	// (ADR-0401), and Show more while there are more.

	type Props = {
		entries: AuditEntry[];
		/** A request is out: the timeline is dimmed and busy. */
		loading: boolean;
		/** More entries match than are shown. */
		more: boolean;
		/** The keys of the actions' and the resource types' texts, by value (the page's maps). */
		actionNames: Record<string, string>;
		resourceTypes: Record<string, string>;
		onshowmore: () => void;
	};

	let { entries, loading, more, actionNames, resourceTypes, onshowmore }: Props = $props();

	const days = $derived(byDay(entries));
</script>

<Card>
	<section
		aria-label={$t('audit.timeline')}
		aria-busy={loading}
		class="space-y-6 transition-opacity"
		class:opacity-60={loading}
	>
		{#each days as group}
			<div>
				<!-- A day's heading as a group label, kept in view while its entries scroll (ADR-0428). -->
				<h2 class="eyebrow sticky top-0 z-[1] -mx-1 px-1 py-1.5 mb-2 bg-surface-elevated">
					<!-- In the language of the page: the timeline follows a change of the language. -->
					<time datetime={group.day}>{dayLabel(group.day, $intlLocale)}</time>
				</h2>
				<ol class="relative ml-1.5 border-l border-border">
					{#each group.entries as entry}
						<AuditEntryItem {entry} {actionNames} {resourceTypes} />
					{/each}
				</ol>
			</div>
		{/each}
	</section>
	{#if more}
		<div class="mt-4 flex justify-center">
			<Button variant="secondary" {loading} onclick={onshowmore}>{$t('audit.showMore')}</Button>
		</div>
	{/if}
</Card>
