<script module lang="ts">
	/** A state of a page that needs the user, with what to do about it. */
	export type StatusItem = {
		tone: 'error' | 'warning' | 'info';
		title: string;
		description?: string;
		action?: { label: string; href?: string; onclick?: () => void };
	};
</script>

<script lang="ts">
	import { AlertCircle, AlertTriangle, ArrowRight, ChevronDown, ChevronUp, Info } from 'lucide-svelte';
	import { t } from '$lib/i18n';
	import Button from './Button.svelte';

	// The states of a page in one bar (ADR-0432), in place of a stack of alerts: the worst tone's
	// colour and how many things need attention, opened to list each with its action. One item shows
	// at once, with its action. Each cause appears once; the page names it on the object too only
	// where the object is.
	type Props = {
		items: StatusItem[];
		/** Opened from the start. */
		open?: boolean;
		class?: string;
	};

	let { items, open = $bindable(false), class: className = '' }: Props = $props();

	const ORDER = { error: 0, warning: 1, info: 2 } as const;
	const ICONS = { error: AlertCircle, warning: AlertTriangle, info: Info };
	const TONE_TEXT = { error: 'text-status-error', warning: 'text-status-warning', info: 'text-status-info' };
	const sorted = $derived([...items].sort((a, b) => ORDER[a.tone] - ORDER[b.tone]));
	const worst = $derived(sorted[0]?.tone ?? 'info');
	const listId = `status-summary-${Math.random().toString(36).slice(2, 9)}`;
</script>

{#snippet actionOf(item: StatusItem)}
	{#if item.action?.href}
		<a href={item.action.href} class="card-action-link"
			>{item.action.label}<ArrowRight class="w-3.5 h-3.5" aria-hidden="true" /></a
		>
	{:else if item.action}
		<Button variant="secondary" size="sm" onclick={item.action.onclick}>{item.action.label}</Button>
	{/if}
{/snippet}

{#if sorted.length === 1}
	{@const item = sorted[0]}
	{@const Icon = ICONS[item.tone]}
	<div class="status-summary alert-{item.tone} {className}" data-status-summary>
		<div class="status-summary-head">
			<Icon class="alert-tone w-[18px] h-[18px] shrink-0" aria-hidden="true" />
			<div class="min-w-0 flex-1">
				<p class="font-medium break-words">{item.title}</p>
				{#if item.description}<p class="text-[13px] text-content-secondary break-words">{item.description}</p>{/if}
			</div>
			{@render actionOf(item)}
		</div>
	</div>
{:else if sorted.length > 1}
	{@const Icon = ICONS[worst]}
	<div class="status-summary alert-{worst} {className}" data-status-summary>
		<div class="status-summary-head">
			<Icon class="alert-tone w-[18px] h-[18px] shrink-0" aria-hidden="true" />
			<p class="min-w-0 flex-1 font-medium">{$t('ui.status.issues', { n: sorted.length })}</p>
			<Button variant="ghost" size="sm" aria-expanded={open} aria-controls={listId} onclick={() => (open = !open)}>
				{open ? $t('ui.status.hide') : $t('ui.status.show')}
				{#if open}<ChevronUp class="w-4 h-4" aria-hidden="true" />{:else}<ChevronDown
						class="w-4 h-4"
						aria-hidden="true"
					/>{/if}
			</Button>
		</div>
		<ul id={listId} class="status-summary-list" hidden={!open}>
			{#each sorted as item, index (index)}
				{@const ItemIcon = ICONS[item.tone]}
				<li class="status-summary-item">
					<ItemIcon class="{TONE_TEXT[item.tone]} w-4 h-4 mt-0.5 shrink-0" aria-hidden="true" />
					<div class="min-w-0 flex-1">
						<p class="break-words">{item.title}</p>
						{#if item.description}<p class="text-[13px] text-content-secondary break-words">{item.description}</p>{/if}
					</div>
					{@render actionOf(item)}
				</li>
			{/each}
		</ul>
	</div>
{/if}
