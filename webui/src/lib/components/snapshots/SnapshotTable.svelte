<script lang="ts" generics="Row extends { name: string; path: string; created: string }">
	// A table of the Snapshots page: the snapshots of a pool or a volume, or the copies that
	// restores made in it, each with its name, its date and the buttons of `actions`. A name that
	// is too long ends in an ellipsis, its full path in its tooltip: the card names the folder once,
	// and no name breaks a letter per line on a phone, where the date goes under the name
	// (ADR-0432).
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
				<th class="w-full">{$t('snapshots.name')}</th>
				<th class="hidden sm:table-cell whitespace-nowrap">{dateHeading}</th>
				<th class="w-px"><span class="sr-only">{$t('snapshots.actions')}</span></th>
			</tr>
		</thead>
		<tbody>
			{#each rows as row (row.path)}
				<tr class="table-row">
					<td class="max-w-0">
						<p class="font-medium truncate" title={row.path}>{row.name}</p>
						<p class="sm:hidden text-xs text-secondary tabular">{formatDate(row.created)}</p>
					</td>
					<td class="hidden sm:table-cell whitespace-nowrap tabular">{formatDate(row.created)}</td>
					<td class="w-px">
						<div class="flex justify-end items-center gap-1 whitespace-nowrap">
							{@render actions(row)}
						</div>
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>
