<script lang="ts">
	import { Languages } from 'lucide-svelte';
	import { Card, Select } from '$lib/components/ui';
	import { LANGUAGES, isLocale, locale, t } from '$lib/i18n';

	// The language of the web UI (ADR-0287). It applies at once, to every page of this browser,
	// and the sign-in page keeps it too: the choice belongs to the browser, not to the account.

	const options = LANGUAGES.map((language) => ({ value: language.code, label: language.name }));

	function choose(event: CustomEvent<{ value: string }>) {
		if (isLocale(event.detail.value)) locale.set(event.detail.value);
	}
</script>

<Card>
	<h3 class="section-title mb-4">
		<Languages class="w-5 h-5" />
		{$t('account.language.title')}
	</h3>
	<Select
		id="language"
		class="max-w-xs"
		label={$t('account.language.label')}
		hint={$t('account.language.hint')}
		value={$locale}
		{options}
		on:change={choose}
	/>
</Card>
