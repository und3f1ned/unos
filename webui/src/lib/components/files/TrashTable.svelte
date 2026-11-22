<script lang="ts">
	import type { TrashItem } from '$lib/api/files';
	import { Checkbox } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import { formatSize } from '$lib/format';
	import { formatDate, formatDateTime } from '$lib/time';
	import { trashItemIcon } from './icons';

	type Props = {
		items: TrashItem[];
		// The paths of the selected items.
		selected: ReadonlySet<string>;
		// An item's checkbox was ticked or cleared.
		ontoggle: (path: string) => void;
	};

	let { items, selected, ontoggle }: Props = $props();

	// The texts that the functions below make follow a change of the language: the markup that
	// calls them reads $t and $intlLocale through them.

	// The server gives UTC times with their offset; the browser shows them in its own time zone,
	// in the language of the page.
	function formatTime(value: string, dateOnly = false): string {
		return dateOnly ? formatDate(value, $intlLocale) : formatDateTime(value, $intlLocale);
	}

	// The folder an item was deleted from.
	function folderOf(item: TrashItem): string {
		if (item.original_path === null) return $t('files.trash.notKnown');
		const cut = item.original_path.lastIndexOf('/');
		return cut > 0 ? item.original_path.slice(0, cut) : '/';
	}

	// What a screen reader says for an item's checkbox: items of the same name
	// differ in where and when they were deleted (ADR-0197), to the second, which
	// the row leaves out.
	function checkboxLabel(item: TrashItem): string {
		const time = formatDateTime(item.deleted, $intlLocale, { seconds: true });
		return item.original_path === null
			? $t('files.trash.selectUnknown', { name: item.name, time })
			: $t('files.trash.select', { name: item.name, folder: folderOf(item), time });
	}
</script>

<!-- The name keeps its words whole and takes the room; the folder it came from ends in an
     ellipsis, whole in its tooltip, and goes under the name on a phone, where Kept until is left out. -->
<div class="table-frame">
	<table class="table" aria-label={$t('files.trash.items')}>
		<thead class="table-header">
			<tr>
				<th class="w-8"><span class="sr-only">{$t('files.trash.column.selected')}</span></th>
				<th class="min-w-[12rem]">{$t('files.column.name')}</th>
				<th class="hidden md:table-cell w-[30%]">{$t('files.trash.column.from')}</th>
				<th class="whitespace-nowrap">{$t('files.trash.column.deleted')}</th>
				<th class="hidden sm:table-cell whitespace-nowrap">{$t('files.trash.column.keptUntil')}</th>
				<th class="num">{$t('files.column.size')}</th>
			</tr>
		</thead>
		<tbody>
			{#each items as item (item.path)}
				{@const Icon = trashItemIcon(item)}
				<tr class="table-row align-top">
					<td>
						<Checkbox
							ariaLabel={checkboxLabel(item)}
							checked={selected.has(item.path)}
							onchange={() => ontoggle(item.path)}
						/>
					</td>
					<td class="min-w-[12rem]">
						<div class="flex items-start gap-2 min-w-0">
							<Icon class="w-4 h-4 mt-0.5 flex-shrink-0 text-content-secondary" />
							<div class="min-w-0">
								<span class="break-words">{item.name}</span>
								<p class="md:hidden mono-xs text-muted truncate max-w-[14rem]" title={folderOf(item)}>
									{folderOf(item)}
								</p>
							</div>
						</div>
					</td>
					<td class="hidden md:table-cell max-w-0">
						<p class="mono-xs truncate" title={folderOf(item)}>{folderOf(item)}</p>
					</td>
					<td class="whitespace-nowrap tabular">{formatTime(item.deleted)}</td>
					<td class="hidden sm:table-cell whitespace-nowrap tabular">{formatTime(item.expires, true)}</td>
					<td class="num whitespace-nowrap"
						>{item.type === 'directory' ? '-' : formatSize(item.size, $intlLocale, $t)}</td
					>
				</tr>
			{/each}
		</tbody>
	</table>
</div>
