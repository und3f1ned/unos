<script lang="ts" generics="Row extends { name: string; path: string; created: string }">
	// A table of the Snapshots page: the snapshots of a pool or a volume, or the copies that
	// restores made in it, each with its name, path and date, and the buttons of `actions`.
	import type { Snippet } from 'svelte';
	import { t } from '$lib/i18n';

	type Props = {
		rows: Row[];
		/** The table's name for screen readers. */
		label: string;
		/** The heading of the date column. */
		dateHeading: string;
		formatDate: (value: string) => string;
		/** The buttons of a row. */
		actions: Snippet<[Row]>;
	};

	let { rows, label, dateHeading, formatDate, actions }: Props = $props();
</script>

<div class="table-frame">
	<table class="table" aria-label={label}>
		<thead class="table-header">
			<tr>
				<th>{$t('snapshots.name')}</th>
				<th>{dateHeading}</th>
				<th><span class="sr-only">{$t('snapshots.actions')}</span></th>
			</tr>
		</thead>
		<tbody>
			{#each rows as row (row.path)}
				<tr class="table-row align-top">
					<td>
						<p class="font-medium break-all">{row.name}</p>
						<p class="mono-xs text-muted break-all">{row.path}</p>
					</td>
					<td class="whitespace-nowrap">{formatDate(row.created)}</td>
					<td>
						<div class="flex justify-end gap-1">
							{@render actions(row)}
						</div>
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>
