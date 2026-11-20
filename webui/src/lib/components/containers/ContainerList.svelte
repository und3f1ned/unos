<script lang="ts">
	// The containers that the Containers page shows, as a table or as cards: a click opens the
	// container's drawer, the checkbox selects it for an action on several (ADR-0389), and the
	// buttons act on it alone. While the list loads, and when it shows none, it says so.
	import {
		Container as ContainerIcon,
		ExternalLink,
		Play,
		RotateCw,
		Square,
		Trash2
	} from 'lucide-svelte';
	import { Badge, Button, Card, EmptyState, type IconComponent } from '$lib/components/ui';
	import type { Container } from '$lib/api/containers';
	import { t } from '$lib/i18n';
	import { labelOf, stateVariant, STATES, webUrl, type QuickAction } from './labels';

	type Props = {
		/** The containers that the search and the filter let through. */
		containers: Container[];
		/** A search or a filter is set: an empty list offers to clear them, else to create one. */
		filtered: boolean;
		view: 'table' | 'cards';
		/** The ids of the selected containers. */
		selected: ReadonlySet<string>;
		/** An action on several containers runs: the selection and the buttons wait for its end. */
		busy: boolean;
		onopen: (container: Container) => void;
		onselect: (id: string, on: boolean) => void;
		/** A container's own button; the page stops the click from opening the drawer. */
		onaction: (event: MouseEvent, id: string, action: QuickAction) => void;
		/** Clear the search and the filter. */
		onclear: () => void;
	};

	let {
		containers,
		filtered,
		view,
		selected,
		busy,
		onopen,
		onselect,
		onaction,
		onclear
	}: Props = $props();

	// The look of a container's own buttons.
	const QUICK: Record<QuickAction, { hover: string; Icon: IconComponent; key: string }> = {
		stop: { hover: 'hover:text-status-warning', Icon: Square, key: 'containers.action.stop' },
		restart: { hover: 'hover:text-status-info', Icon: RotateCw, key: 'containers.action.restart' },
		start: { hover: 'hover:text-status-success', Icon: Play, key: 'containers.action.start' },
		remove: { hover: 'hover:text-status-error', Icon: Trash2, key: 'containers.action.remove' }
	};

	function formatPorts(container: Container): string {
		if (container.ports.length === 0) return '-';
		return container.ports
			.filter((p) => p.host_port)
			.map((p) => `${p.host_port}:${p.container_port}`)
			.join(', ');
	}
</script>

<!-- Selecting does not open the drawer; on a card, neither does Enter on the checkbox. -->
{#snippet selectBox(container: Container, className: string, onCard: boolean)}
	<input
		type="checkbox"
		class={className}
		checked={selected.has(container.id)}
		disabled={busy}
		aria-label={$t('containers.bulk.selectOne', { name: container.name })}
		onclick={(e) => e.stopPropagation()}
		onkeypress={onCard ? (e) => e.stopPropagation() : undefined}
		onchange={(event) => onselect(container.id, event.currentTarget.checked)}
	/>
{/snippet}

{#snippet webLink(href: string, className: string, iconClass: string)}
	<a
		{href}
		target="_blank"
		rel="noopener"
		onclick={(e) => e.stopPropagation()}
		class={className}
		title={$t('containers.openInBrowser')}
	>
		<ExternalLink class={iconClass} />
	</a>
{/snippet}

<!-- A container's own button. -->
{#snippet quick(container: Container, action: QuickAction)}
	{@const look = QUICK[action]}
	<Button variant="ghost" size="icon-sm" class="text-secondary {look.hover}"
		disabled={busy}
		onclick={(e) => onaction(e, container.id, action)}
		title={$t(look.key)}
	>
		<look.Icon class="w-4 h-4" />
	</Button>
{/snippet}

<!-- A container's own buttons, the same in a row and a card: its one place of them, the drawer
     has none (ADR-0431). -->
{#snippet actions(container: Container)}
	{#if container.state === 'running'}
		{@render quick(container, 'stop')}
		{@render quick(container, 'restart')}
	{:else}
		{@render quick(container, 'start')}
	{/if}
	{@render quick(container, 'remove')}
{/snippet}

{#if containers.length === 0}
	<EmptyState
		icon={ContainerIcon}
		title={$t('containers.empty.title')}
		description={filtered
			? $t('containers.empty.filtered')
			: $t('containers.empty.none')}
	>
		<!-- Create Container is the page header's (ADR-0431). -->
		{#if filtered}
			<Button
				variant="secondary"
				onclick={onclear}>{$t('containers.empty.clear')}</Button
			>
		{/if}
	</EmptyState>
{:else if view === 'table'}
	<!-- Table View: a flush card (ADR-0428) -->
	<Card flush>
		<div class="table-frame">
			<table class="table">
				<thead class="table-header">
					<tr>
						<th class="w-12"></th>
						<th>{$t('containers.column.name')}</th>
						<th>{$t('containers.column.image')}</th>
						<th>{$t('containers.column.state')}</th>
						<th>{$t('containers.column.ports')}</th>
						<th class="text-right">{$t('containers.column.actions')}</th>
					</tr>
				</thead>
				<tbody>
					{#each containers as container}
						{@const href = webUrl(container)}
						<tr
							class="table-row hover:bg-surface-overlay cursor-pointer transition-colors"
							onclick={() => onopen(container)}
						>
							<td>
								{@render selectBox(container, 'form-checkbox', false)}
							</td>
							<td>
								<div class="font-medium">{container.name}</div>
								<div class="text-xs text-muted mono-xs truncate max-w-48">
									{container.id.substring(0, 12)}
								</div>
							</td>
							<td>
								<div class="mono text-sm truncate max-w-48">{container.image}</div>
							</td>
							<td>
								<Badge variant={stateVariant(container.state)} dot>{labelOf(STATES, container.state, $t)}</Badge>
							</td>
							<td>
								<div class="flex items-center gap-2">
									<span class="text-sm text-secondary mono-xs">{formatPorts(container)}</span>
									{#if href}
										{@render webLink(href, 'text-content-link', 'w-3.5 h-3.5')}
									{/if}
								</div>
							</td>
							<td>
								<div class="table-actions">
									{@render actions(container)}
								</div>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</Card>
{:else}
	<!-- Cards View -->
	<div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
		{#each containers as container}
			{@const href = webUrl(container)}
			<div
				class="card hover:bg-surface-overlay cursor-pointer transition-colors"
				onclick={() => onopen(container)}
				role="button"
				tabindex="0"
				onkeypress={(e) => e.key === 'Enter' && onopen(container)}
			>
				<div class="flex items-start justify-between mb-3">
					<div class="flex items-center gap-3 min-w-0">
						{@render selectBox(container, 'form-checkbox flex-shrink-0', true)}
						<div
							class="w-10 h-10 rounded-lg bg-surface-overlay flex items-center justify-center flex-shrink-0"
							aria-hidden="true"
						>
							<ContainerIcon class="w-5 h-5 text-content-secondary" />
						</div>
						<div class="min-w-0">
							<h3 class="font-semibold truncate">{container.name}</h3>
							<p class="text-sm text-secondary truncate">{container.image}</p>
						</div>
					</div>
					<Badge variant={stateVariant(container.state)}>{labelOf(STATES, container.state, $t)}</Badge>
				</div>

				{#if container.ports.length > 0}
					<div class="mb-3">
						<p class="text-xs text-muted mb-1">{$t('containers.column.ports')}</p>
						<div class="flex flex-wrap gap-1">
							{#each container.ports.filter((p) => p.host_port) as port}
								<span class="badge badge-info">
									{port.host_port}:{port.container_port}
								</span>
							{/each}
						</div>
					</div>
				{/if}

				<div class="flex items-center justify-between pt-3 border-t border-border">
					<span class="mono-xs text-muted">{container.id.substring(0, 12)}</span>
					<div class="flex items-center gap-1">
						{#if href}
							{@render webLink(href, 'p-1.5 rounded hover:bg-surface-muted text-info transition-colors', 'w-4 h-4')}
						{/if}
						{@render actions(container)}
					</div>
				</div>
			</div>
		{/each}
	</div>
{/if}
