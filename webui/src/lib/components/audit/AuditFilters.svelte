<script lang="ts">
	import { X } from 'lucide-svelte';
	import { Button, Card, Input, Select } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	// The filters of the audit log (ADR-0401): a search of the entries' words, the user, the
	// action, and From and To, the browser's days; how many entries match, and Clear.

	const SEARCH_MAX_CHARS = 256; // MAX_SEARCH_CHARS of supervisor/src/audit/router.py

	type Props = {
		search: string;
		username: string;
		action: string;
		/** The days of From and To, YYYY-MM-DD as a date field holds them, or ''. */
		startDate: string;
		endDate: string;
		/** The actions to choose, with All actions first. */
		actionOptions: { value: string; label: string }[];
		/** Why the actions could not be read; '' when they were. */
		actionsError: string;
		/** How many entries match, once they are known; else ''. */
		count: string;
		/** A filter is set: Clear is offered. */
		filtered: boolean;
		onclear: () => void;
	};

	let {
		search = $bindable(),
		username = $bindable(),
		action = $bindable(),
		startDate = $bindable(),
		endDate = $bindable(),
		actionOptions,
		actionsError,
		count,
		filtered,
		onclear
	}: Props = $props();
</script>

<Card>
	<div role="search" class="grid gap-4 sm:grid-cols-2 xl:grid-cols-6">
		<Input
			id="audit-search"
			type="search"
			label={$t('audit.filter.search')}
			bind:value={search}
			placeholder={$t('audit.filter.searchPlaceholder')}
			maxlength={SEARCH_MAX_CHARS}
			class="sm:col-span-2"
		/>
		<Input
			id="audit-user"
			label={$t('audit.filter.user')}
			bind:value={username}
			placeholder={$t('audit.filter.userPlaceholder')}
			autocomplete="off"
		/>
		<Select
			id="audit-action"
			label={$t('audit.filter.action')}
			bind:value={action}
			options={actionOptions}
			error={actionsError && $t('audit.filter.actionsError', { message: actionsError })}
		/>
		<Input id="audit-from" type="date" label={$t('audit.filter.from')} bind:value={startDate} />
		<Input id="audit-to" type="date" label={$t('audit.filter.to')} bind:value={endDate} />
	</div>
	<div class="mt-3 flex min-h-8 items-center justify-between gap-2">
		<p class="text-sm text-content-secondary" role="status">{count}</p>
		{#if filtered}
			<Button variant="ghost" size="sm" icon={X} onclick={onclear}>{$t('audit.filter.clear')}</Button>
		{/if}
	</div>
</Card>
