<script lang="ts">
	// A skeleton (ADR-0430): the shape of what is loading, drawn where it will be, so that the data
	// arriving moves nothing. Hidden from screen readers and without text or role: the region that
	// loads says so with aria-busy. Its bones shimmer together (app.css .skeleton); under reduced
	// motion they stand still.
	//
	// line   lines of text of a size, each as tall as the text's line (`count` lines, the last one shorter)
	// block  a rectangle: a chart, a field, a button, an image (its size from `class`)
	// circle an avatar or an icon (its size from `class`)
	// row    rows of a table (`count` rows of `columns` cells), as tall as a table's, in a card
	//        (divs: no table, row or cell that a spec's CSS or role could find)
	// tile   a stat tile of the dashboard, as tall as the tallest one
	// card   a card: its title and `count` lines
	// table  a card that holds a table (Card flush): its title, the head and `count` rows of `columns`
	//
	// A page shows its skeleton in place of everything that its first load brings, and swaps it for
	// the data at once: no part of it stays to be pushed down by what came (ADR-0430).

	type Size = 'eyebrow' | 'xs' | 'sm' | 'base' | 'lg' | 'xl' | 'title' | 'stat' | 'control';
	type Props = {
		shape?: 'line' | 'block' | 'circle' | 'row' | 'tile' | 'card' | 'table';
		/**
		 * line and row: the text whose line it takes the height of, by Tailwind's text sizes; eyebrow
		 * the 11 px group labels, title a page's title, stat a stat tile's value, control a button's or
		 * a field's 32 px.
		 */
		size?: Size;
		/** line and card: the lines; row and table: the rows. */
		count?: number;
		/** row and table: the cells of a row. */
		columns?: number;
		class?: string;
	};

	let { shape = 'line', size = 'sm', count = 1, columns = 3, class: className = '' }: Props = $props();

	// Each line as tall as the line of its text, the bone about as tall as the letters.
	const LINE: Record<Size, [string, string]> = {
		eyebrow: ['h-3.5', 'h-2'],
		xs: ['h-4', 'h-2.5'],
		sm: ['h-5', 'h-3'],
		base: ['h-6', 'h-3.5'],
		lg: ['h-7', 'h-4'],
		xl: ['h-7', 'h-[18px]'],
		title: ['h-8', 'h-5'],
		stat: ['h-8 md:h-[34px]', 'h-5 md:h-6'],
		control: ['h-8', 'h-3']
	};
	// Lines of a paragraph and cells of a table differ in length, as text does.
	const LINE_WIDTHS = ['100%', '92%', '96%', '84%', '90%'];
	const CELL_WIDTHS = ['72%', '48%', '60%', '36%', '54%', '40%'];

	const lines = $derived(Array.from({ length: Math.max(1, count) }, (_, i) =>
		count > 1 && i === count - 1 ? '62%' : LINE_WIDTHS[i % LINE_WIDTHS.length]
	));
	const rows = $derived(Array.from({ length: Math.max(1, count) }, (_, i) => i));
	const cells = $derived(Array.from({ length: Math.max(1, columns) }, (_, i) => CELL_WIDTHS[i % CELL_WIDTHS.length]));
</script>

{#snippet line(size: Size, width: string)}
	<span class="flex items-center {LINE[size][0]}">
		<span class="skeleton {LINE[size][1]}" style:width></span>
	</span>
{/snippet}

<!-- A row of cells as a table's: 10 px above and below, the cells 24 px apart. -->
{#snippet cellsOf(size: Size, head: boolean)}
	{#each cells as width, i (i)}
		<span class="flex-1 min-w-0">
			{@render line(head ? 'xs' : size, head ? (i === 0 ? '40%' : '30%') : i === cells.length - 1 && cells.length > 2 ? '28%' : width)}
		</span>
	{/each}
{/snippet}

{#snippet rowsOf(size: Size, inset: string)}
	{#each rows as row (row)}
		<div class="flex items-center gap-6 py-2.5 {inset} border-b border-border-muted last:border-b-0">
			{@render cellsOf(size, false)}
		</div>
	{/each}
{/snippet}

{#if shape === 'row'}
	<div class={className} data-skeleton aria-hidden="true">{@render rowsOf(size, '')}</div>
{:else if shape === 'table'}
	<div class="card card-flush {className}" data-skeleton aria-hidden="true">
		<div class="card-header">{@render line('base', '9rem')}</div>
		<div class="flex items-center gap-6 py-2.5 px-5 border-b border-border">{@render cellsOf(size, true)}</div>
		{@render rowsOf(size, 'px-5')}
	</div>
{:else if shape === 'card'}
	<div class="card {className}" data-skeleton aria-hidden="true">
		<div class="card-header">{@render line('base', '9rem')}</div>
		{#each lines as width, i (i)}
			{@render line(size, width)}
		{/each}
	</div>
{:else if shape === 'tile'}
	<!-- The structure of the tallest stat tile, memory's: header, value, bar and a line under it. -->
	<div class="card stat-card {className}" data-skeleton aria-hidden="true">
		<div class="stat-card-header">
			<span class="skeleton stat-chip"></span>
			<span class="flex-1 min-w-0">{@render line('sm', '45%')}</span>
		</div>
		{@render line('stat', '55%')}
		<span class="mt-auto pt-1"><span class="skeleton block h-1 w-full rounded-full"></span></span>
		{@render line('xs', '50%')}
	</div>
{:else if shape === 'block'}
	<span class="skeleton block {className || 'h-24 w-full'}" data-skeleton aria-hidden="true"></span>
{:else if shape === 'circle'}
	<span class="skeleton block shrink-0 rounded-full {className || 'w-8 h-8'}" data-skeleton aria-hidden="true"></span>
{:else}
	<span class="block {className}" data-skeleton aria-hidden="true">
		{#each lines as width, i (i)}
			{@render line(size, width)}
		{/each}
	</span>
{/if}
