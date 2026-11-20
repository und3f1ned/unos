<script lang="ts">
	import { SlidersHorizontal, X } from 'lucide-svelte';
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
		/** How many filters are set, for the phone's Filters button. */
		active: number;
		/** The names the User field suggests (the NAS's users); any other name can be typed. */
		usernames?: string[];
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
		active,
		usernames = [],
		onclear
	}: Props = $props();

	// On a phone the fields fold behind Filters, so that the entries start on the first screen;
	// from sm they are always shown.
	let open = $state(false);
</script>

<Card>
	<div class="mb-3 sm:hidden">
		<Button
			variant="secondary"
			size="sm"
			icon={SlidersHorizontal}
			aria-expanded={open}
			aria-controls="audit-filters"
			onclick={() => (open = !open)}
		>
			{active > 0 ? $t('audit.filter.toggleCount', { n: active }) : $t('audit.filter.toggle')}
		</Button>
	</div>
	<div role="search" id="audit-filters" class="gap-4 sm:grid sm:grid-cols-2 xl:grid-cols-6 {open ? 'grid' : 'hidden'}">
		<Input
			id="audit-search"
			type="search"
			label={$t('audit.filter.search')}
			bind:value={search}
			placeholder={$t('audit.filter.searchPlaceholder')}
			maxlength={SEARCH_MAX_CHARS}
			class="sm:col-span-2"
		/>
		<!-- A raw field for its list of suggestions (datalist), which Input does not take. -->
		<div>
			<label for="audit-user" class="form-label">{$t('audit.filter.user')}</label>
			<input
				id="audit-user"
				class="form-input"
				type="text"
				list="audit-user-names"
				bind:value={username}
				placeholder={$t('audit.filter.userPlaceholder')}
				autocomplete="off"
			/>
			<datalist id="audit-user-names">
				{#each usernames as name (name)}
					<option value={name}></option>
				{/each}
			</datalist>
		</div>
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
	<div class="flex min-h-8 items-center justify-between gap-2 sm:mt-3 {open ? 'mt-3' : ''}">
		<p class="text-sm text-content-secondary" role="status">{count}</p>
		{#if filtered}
			<Button variant="ghost" size="sm" icon={X} onclick={onclear}>{$t('audit.filter.clear')}</Button>
		{/if}
	</div>
</Card>
