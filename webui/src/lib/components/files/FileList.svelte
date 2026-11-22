<script lang="ts">
	import { tick } from 'svelte';
	import { ArrowUp } from 'lucide-svelte';
	import type { FileInfo } from '$lib/api/files';
	import { Button, Checkbox, Input } from '$lib/components/ui';
	import { formatSize } from '$lib/format';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDate } from '$lib/time';
	import type { FileBrowser } from './browser.svelte';
	import { fileIcon } from './icons';

	type Props = {
		browser: FileBrowser;
	};

	let { browser }: Props = $props();

	// The columns of the list and its header: the check box, the name, the size and, from sm up,
	// the day of the last change. Fixed widths, so that the rows, each a grid, line up.
	const columnsClass = 'grid-cols-[auto_minmax(0,1fr)_5rem] sm:grid-cols-[auto_1fr_100px_150px]';

	// The ".." entry, the first one of the list of a folder (state: it comes and goes with the
	// folder's parent, and a component's binding needs it).
	let upButton: HTMLButtonElement | null = $state(null);

	// The list of the open folder: the ".." entry and the items.
	let listBox: HTMLDivElement | null = null;

	// An item's name is a button for the keyboard and screen readers: Enter,
	// Space or a screen reader's click opens the item, like a double-click.
	// The mouse, pen or finger never reaches it (pointer-events-none): their
	// clicks go to the row, which selects the item, and a double-click opens it.
	// The event cannot tell a screen reader's click from a mouse click: Chrome
	// sends it with detail 1 and pointerType "mouse".
	async function openFromName(event: MouseEvent, item: FileInfo) {
		event.stopPropagation();
		await browser.open(item);
		// The focused name went away with the old list: go on at the top of the new one.
		await tick();
		if (document.activeElement === null || document.activeElement === document.body) {
			upButton?.focus();
		}
	}

	// Whether the keyboard focus is shown on `element`. Chrome also focuses a
	// button that is clicked with the mouse, but does not show it.
	function showsFocus(element: HTMLElement | null): boolean {
		if (element === null || element !== document.activeElement) return false;
		try {
			return element.matches(':focus-visible');
		} catch {
			return true; // a browser without :focus-visible
		}
	}

	async function goUp() {
		const parent = browser.listing?.parent;
		if (!parent) return;
		const fromKeyboard = showsFocus(upButton);
		await browser.navigate(parent);
		if (!fromKeyboard) return;
		// The pools directory has no ".." entry, so the focus went with it: go on
		// at the first item's name, or at the list when there is none.
		await tick();
		if (document.activeElement !== null && document.activeElement !== document.body) return;
		const firstName = listBox?.querySelector<HTMLElement>('button');
		if (firstName) {
			firstName.focus();
		} else if (listBox) {
			// Focusable only until it loses the focus: a list with a tabindex would
			// take the focus of every mouse click on a row. Leaving the window also
			// sends a blur, but the list keeps the focus for the return.
			const list = listBox;
			const release = () => {
				if (document.activeElement === list) return;
				list.removeAttribute('tabindex');
				list.removeEventListener('blur', release);
			};
			list.tabIndex = -1;
			list.addEventListener('blur', release);
			list.focus();
		}
	}

	// Enter renames, Escape keeps the name. Either way the keyboard focus goes on
	// at the item's checkbox, which a screen reader reads with the item's name.
	async function nameFieldKey(event: KeyboardEvent, item: FileInfo) {
		if (event.key === 'Escape') {
			browser.renaming = null;
			await focusCheckbox(item.path);
		} else if (event.key === 'Enter') {
			const path = await browser.rename(item);
			if (path !== null) await focusCheckbox(path);
		}
	}

	// Leaving the field renames the item. Shift+Tab leaves it for the item's
	// checkbox, whose row the rename replaces: the focus goes on at the new
	// row's checkbox. The field also gets a blur when the window or the tab
	// loses the focus (another app or tab, the address bar), while the page has
	// no focus: the field gets the focus back on the return, so it stays open,
	// as typed.
	async function nameFieldBlur(item: FileInfo) {
		if (!document.hasFocus()) return;
		const path = await browser.rename(item);
		if (path !== null) await focusCheckbox(path);
	}

	async function focusCheckbox(path: string) {
		await tick();
		// Unless the focus has gone elsewhere meanwhile.
		if (document.activeElement !== null && document.activeElement !== document.body) return;
		const boxes = Array.from(listBox?.querySelectorAll<HTMLElement>('[data-path]') ?? []);
		boxes
			.find((box) => box.dataset.path === path)
			?.querySelector('input')
			?.focus();
	}
</script>

<!-- File List -->
<!-- A framed table on the grid (ADR-0428): 40 px rows, the selected one a brand tint. -->
<div
	class="md:flex-1 md:min-h-0 flex flex-col bg-surface-elevated rounded-card border border-border shadow-raised overflow-hidden"
>
	<div class="grid {columnsClass} gap-4 px-4 py-2.5 text-xs font-medium text-content-secondary border-b border-border">
		<div class="w-6"></div>
		<div>{$t('files.column.name')}</div>
		<div>{$t('files.column.size')}</div>
		<div class="hidden sm:block">{$t('files.column.modified')}</div>
	</div>

	<div bind:this={listBox} class="md:flex-1 md:min-h-0 overflow-y-auto" role="region" aria-label={$t('files.contents')}>
		{#if browser.listing?.parent}
			<Button
				variant="plain"
				bind:el={upButton}
				onclick={goUp}
				class="w-full grid {columnsClass} gap-4 px-4 min-h-10 py-1.5 hover:bg-surface-overlay/60 items-center text-left border-b border-border-muted"
				title={$t('files.up')}
			>
				<ArrowUp class="w-[18px] h-[18px] text-content-secondary" />
				<span class="text-content-secondary">..</span>
				<span></span>
				<span class="hidden sm:block"></span>
			</Button>
		{/if}

		{#if browser.listing?.items}
			{#each browser.listing.items as item (item.path)}
				{@const Icon = fileIcon(item)}
				<!-- Mouse shortcuts: a click on the row selects the item, a double-click
             opens it. From the keyboard, the checkbox selects and the name opens. -->
				<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
				<div
					class="grid {columnsClass} gap-4 px-4 min-h-10 py-1.5 hover:bg-surface-overlay/60 items-center border-b border-border-muted last:border-b-0 cursor-pointer {browser.selected.has(
						item.path
					)
						? 'bg-brand/20'
						: ''}"
					onclick={() => browser.toggleSelect(item.path)}
					ondblclick={() => browser.open(item)}
				>
					<!-- The checkbox's click stays out of the row, which would select the item a second time. -->
					<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
					<span class="inline-flex" data-path={item.path} onclick={(e) => e.stopPropagation()}>
						<Checkbox
							ariaLabel={$t('files.select', { name: item.name })}
							checked={browser.selected.has(item.path)}
							onchange={() => browser.toggleSelect(item.path)}
						/>
					</span>
					<div class="flex items-center gap-3 min-w-0">
						<Icon class="w-[18px] h-[18px] flex-shrink-0 text-content-secondary" />
						{#if browser.renaming === item.path}
							<!-- Clicks in the field, a double-click on a word too, stay out of the row. -->
							<Input
								bind:value={browser.renamingValue}
								ariaLabel={$t('files.newName', { name: item.name })}
								autocomplete="off"
								spellcheck={false}
								size="sm"
								selectOnMount
								class="min-w-0 flex-1"
								onkeydown={(e) => nameFieldKey(e, item)}
								onblur={() => nameFieldBlur(item)}
								onclick={(e) => e.stopPropagation()}
								ondblclick={(e) => e.stopPropagation()}
							/>
						{:else if item.type === 'directory' || item.type === 'file'}
							<Button
								variant="ghost"
								size="sm"
								class="pointer-events-none min-w-0 max-w-full min-h-0 justify-start -mx-1 px-1 py-0.5 text-sm {item.type ===
								'directory'
									? 'font-medium'
									: 'font-normal'} text-content hover:bg-transparent"
								onclick={(e) => openFromName(e, item)}
							>
								<span class="sr-only"
									>{$t(item.type === 'directory' ? 'files.openItem' : 'files.downloadItem', { name: item.name })}</span
								>
								<span class="truncate" aria-hidden="true">{item.name}</span>
							</Button>
						{:else}
							<span class="truncate">{item.name}</span>
						{/if}
					</div>
					<span class="text-sm text-content-secondary tabular">
						<!-- A folder's size is not known without walking it: the cell stays empty rather than "-". -->
						{item.type === 'directory' ? '' : formatSize(item.size, $intlLocale, $t)}
					</span>
					<span class="hidden sm:block text-sm text-content-secondary tabular">
						{formatDate(item.modified, $intlLocale)}
					</span>
				</div>
			{/each}
		{/if}
	</div>
</div>

<!-- Status Bar -->
{#if browser.listing}
	<div class="mt-2 text-sm text-content-secondary flex justify-between">
		<span>{$t('files.itemCount', { n: browser.listing.item_count })}</span>
		<span>{formatSize(browser.listing.total_size, $intlLocale, $t)}</span>
	</div>
{/if}
