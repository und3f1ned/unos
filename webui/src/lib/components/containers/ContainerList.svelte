<script lang="ts">
	// The containers that the Containers page shows, as a table or as cards: a click opens the
	// container's details (a Sheet), the checkbox selects it for an action on several (ADR-0389), and the
	// buttons and its ⋯ menu act on it alone. When it shows none, it says so. A row's buttons stay on one line in
	// a column as narrow as they are, the name keeps a width, and an image cut short shows whole on
	// hover (webui.ux-apps). A container that an app of the App Store made says so and links there;
	// it has no Remove: removing one container breaks its app, whose Uninstall is the App Store's.
	import { Container as ContainerIcon, ExternalLink, Package, Play, RotateCw, Square, Trash2 } from 'lucide-svelte';
	import {
		ActionMenu,
		Badge,
		Button,
		Card,
		Checkbox,
		EmptyState,
		type IconComponent,
		type MenuItem
	} from '$lib/components/ui';
	import type { Container } from '$lib/api/containers';
	import { t } from '$lib/i18n';
	import { appOf, labelOf, stateVariant, STATES, webUrl, type QuickAction } from './labels';

	type Props = {
		/** The containers that the search and the filter let through. */
		containers: Container[];
		/** A search or a filter is set: an empty list offers to clear them. */
		filtered: boolean;
		view: 'table' | 'cards';
		/** The ids of the selected containers. */
		selected: ReadonlySet<string>;
		/** An action on several containers runs: the selection and the buttons wait for its end. */
		busy: boolean;
		onopen: (container: Container) => void;
		onselect: (id: string, on: boolean) => void;
		/** Select every container shown, or none. */
		ontoggleall: (on: boolean) => void;
		/** A container's own button or menu item. */
		onaction: (id: string, action: QuickAction) => void;
		/** Clear the search and the filter. */
		onclear: () => void;
	};

	let { containers, filtered, view, selected, busy, onopen, onselect, ontoggleall, onaction, onclear }: Props =
		$props();

	const allSelected = $derived(containers.length > 0 && containers.every((c) => selected.has(c.id)));
	const someSelected = $derived(!allSelected && containers.some((c) => selected.has(c.id)));

	// The look of a container's own buttons: no status colours on an action (webui.ux-apps).
	const QUICK: Record<'start' | 'stop', { Icon: IconComponent; key: string }> = {
		stop: { Icon: Square, key: 'containers.action.stop' },
		start: { Icon: Play, key: 'containers.action.start' }
	};

	// i18n-ignore: a dash for no port, the same in every language
	const NO_PORTS = '–';

	function hostPorts(container: Container): string[] {
		return container.ports.filter((p) => p.host_port).map((p) => `${p.host_port}:${p.container_port}`);
	}

	/**
	 * The event comes from a control of a row or a card (its checkbox, a button, a link, its ⋯ menu),
	 * which does its own thing and opens nothing.
	 */
	function fromControl(event: Event): boolean {
		return event.target instanceof Element && event.target.closest('input, button, a, [role="menu"]') !== null;
	}

	/**
	 * The actions of a container's ⋯ menu (ADR-0432, ADR-0437): Restart while it runs, and Remove,
	 * last, unless an app of the App Store made it.
	 */
	function moreOf(container: Container): MenuItem[] {
		const running = container.state === 'running' || container.state === 'restarting';
		return [
			...(running
				? [{ label: $t('containers.action.restart'), icon: RotateCw, onclick: () => onaction(container.id, 'restart') }]
				: []),
			...(appOf(container) === null
				? [
						{
							label: $t('containers.action.remove'),
							icon: Trash2,
							danger: true,
							onclick: () => onaction(container.id, 'remove')
						}
					]
				: [])
		];
	}

	function appName(container: Container): string | null {
		const app = appOf(container);
		return app === null ? null : (container.labels['unos.app.name'] ?? app);
	}
</script>

<!-- Selecting does not open the drawer: the row and the card leave a press on a checkbox alone. -->
{#snippet selectBox(container: Container, className: string)}
	<Checkbox
		checked={selected.has(container.id)}
		disabled={busy}
		ariaLabel={$t('containers.bulk.selectOne', { name: container.name })}
		class={className}
		onchange={(on) => onselect(container.id, on)}
	/>
{/snippet}

<!-- Every container shown, or none; part of them shows as indeterminate. -->
{#snippet selectAll(withLabel: boolean)}
	<Checkbox
		checked={allSelected}
		indeterminate={someSelected}
		disabled={busy}
		ariaLabel={withLabel ? '' : $t('containers.bulk.selectAll')}
		onchange={(on) => ontoggleall(on)}
	/>
{/snippet}

{#snippet webLink(href: string)}
	<Button
		variant="ghost"
		size="icon-sm"
		{href}
		target="_blank"
		rel="noopener"
		onclick={(e) => e.stopPropagation()}
		title={$t('containers.openInBrowser')}
	>
		<ExternalLink class="w-4 h-4" aria-hidden="true" />
		<span class="sr-only">{$t('containers.openInBrowser')}</span>
	</Button>
{/snippet}

<!-- The app of the App Store that made the container: a link to its card there. -->
{#snippet appLink(name: string)}
	<a
		href="/apps#installed"
		class="inline-flex items-center gap-1 text-xs text-content-secondary hover:text-content-link hover:underline"
		onclick={(e) => e.stopPropagation()}
	>
		<Package class="w-3 h-3" aria-hidden="true" />
		{$t('containers.app', { name })}
	</a>
{/snippet}

<!-- A container's own button. -->
{#snippet quick(container: Container, action: 'start' | 'stop')}
	{@const look = QUICK[action]}
	<Button
		variant="ghost"
		size="icon-sm"
		disabled={busy}
		onclick={() => onaction(container.id, action)}
		title={$t(look.key)}
	>
		<look.Icon class="w-4 h-4" aria-hidden="true" />
		<span class="sr-only">{$t(look.key)}</span>
	</Button>
{/snippet}

<!-- A container's own buttons, the same in a row and a card: its one place of them, the drawer
     has none (ADR-0431). -->
{#snippet actions(container: Container, href: string | null)}
	<!-- Each button keeps its column, so that the rows' buttons line up: an empty slot of the same
	     size where a container has no web page or nothing for its ⋯ menu (no Restart, as it does not
	     run, and no Remove, as an app's). Open in browser and Start or Stop are buttons; Restart
	     and Remove are its ⋯ menu's (ADR-0437). -->
	{#if href}{@render webLink(href)}{:else}{@render slot()}{/if}
	<!-- A container that restarts over and over is stopped like a running one: Start would do
	     nothing to it. -->
	{#if container.state === 'running' || container.state === 'restarting'}
		{@render quick(container, 'stop')}
	{:else}
		{@render quick(container, 'start')}
	{/if}
	{@const more = moreOf(container)}
	{#if more.length > 0}
		<ActionMenu items={more} name={container.name} disabled={busy} />
	{:else}
		{@render slot()}
	{/if}
{/snippet}

{#snippet slot()}<span class="inline-block w-8 h-8 shrink-0" aria-hidden="true"></span>{/snippet}

{#snippet state(container: Container)}
	<Badge variant={stateVariant(container.state)} dot>{labelOf(STATES, container.state, $t)}</Badge>
{/snippet}

{#if containers.length === 0}
	<EmptyState
		icon={ContainerIcon}
		title={filtered ? $t('containers.empty.filteredTitle') : $t('containers.empty.title')}
		description={filtered ? $t('containers.empty.filtered') : $t('containers.empty.none')}
	>
		<!-- Create container is the page header's (ADR-0431). -->
		{#if filtered}
			<Button variant="secondary" onclick={onclear}>{$t('containers.empty.clear')}</Button>
		{/if}
	</EmptyState>
{:else if view === 'table'}
	<!-- Table View: a flush card (ADR-0428) -->
	<Card flush>
		<div class="table-frame">
			<table class="table">
				<thead class="table-header">
					<tr>
						<th class="w-10">{@render selectAll(false)}</th>
						<th>{$t('containers.column.name')}</th>
						<th>{$t('containers.column.image')}</th>
						<th>{$t('containers.column.state')}</th>
						<th>{$t('containers.column.ports')}</th>
						<th class="w-px text-right"><span class="sr-only">{$t('containers.column.actions')}</span></th>
					</tr>
				</thead>
				<tbody>
					{#each containers as container (container.id)}
						{@const href = webUrl(container)}
						{@const app = appName(container)}
						<tr
							class="table-row cursor-pointer {selected.has(container.id) ? 'bg-surface-overlay/60' : ''}"
							onclick={(e) => !fromControl(e) && onopen(container)}
						>
							<td>
								{@render selectBox(container, '')}
							</td>
							<td class="min-w-[16ch]">
								<div class="font-medium break-words">{container.name}</div>
								<div class="flex flex-wrap items-center gap-x-3 gap-y-0.5">
									<span class="mono-xs text-content-muted">{container.id.substring(0, 12)}</span>
									{#if app}{@render appLink(app)}{/if}
								</div>
							</td>
							<td>
								<div class="mono text-sm truncate max-w-[24ch]" title={container.image}>{container.image}</div>
							</td>
							<td class="whitespace-nowrap">{@render state(container)}</td>
							<td>
								<span class="mono-xs text-content-secondary">{hostPorts(container).join(', ') || NO_PORTS}</span>
							</td>
							<td class="w-px whitespace-nowrap">
								<div class="table-actions flex-nowrap">
									{@render actions(container, href)}
								</div>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</Card>
{:else}
	<!-- Cards View: the phone's default; every card of a row as tall as the tallest. -->
	<div class="space-y-3">
		<label class="flex items-center gap-2 text-sm text-content-secondary w-fit">
			{@render selectAll(true)}
			{$t('containers.bulk.selectAll')}
		</label>
		<div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
			{#each containers as container (container.id)}
				{@const href = webUrl(container)}
				{@const app = appName(container)}
				{@const ports = hostPorts(container)}
				<div
					class="card flex h-full flex-col gap-3 hover:bg-surface-overlay cursor-pointer transition-[color,background-color,border-color,text-decoration-color,fill,stroke] {selected.has(
						container.id
					)
						? 'ring-1 ring-brand-ring'
						: ''}"
					onclick={(e) => !fromControl(e) && onopen(container)}
					role="button"
					tabindex="0"
					onkeypress={(e) => e.key === 'Enter' && !fromControl(e) && onopen(container)}
				>
					<div class="flex items-start gap-3">
						{@render selectBox(container, 'mt-1 flex-shrink-0')}
						<div class="min-w-0 flex-1">
							<h3 class="font-semibold break-words">{container.name}</h3>
							<p class="mono-xs text-content-secondary truncate" title={container.image}>{container.image}</p>
						</div>
						<div class="flex-shrink-0">{@render state(container)}</div>
					</div>
					{#if ports.length > 0 || app}
						<div class="flex flex-wrap items-center gap-x-3 gap-y-1">
							{#if ports.length > 0}
								<span class="mono-xs text-content-secondary">{ports.join(', ')}</span>
							{/if}
							{#if app}{@render appLink(app)}{/if}
						</div>
					{/if}
					<div class="mt-auto flex items-center justify-between gap-2 pt-3 border-t border-border">
						<span class="mono-xs text-content-muted">{container.id.substring(0, 12)}</span>
						<div class="table-actions flex-nowrap">
							{@render actions(container, href)}
						</div>
					</div>
				</div>
			{/each}
		</div>
	</div>
{/if}
