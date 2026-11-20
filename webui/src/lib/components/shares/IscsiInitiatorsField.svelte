<script lang="ts">
	import { t } from '$lib/i18n';

	// The initiators that may log in to a target, one IQN per line: a raw textarea, which Input
	// does not offer.
	type Props = {
		id: string;
		value: string;
		placeholder?: string | undefined;
		/** Why the list cannot be used; undefined: the form checks nothing here (no error line). */
		error?: string | undefined;
	};

	let { id, value = $bindable(), placeholder = undefined, error = undefined }: Props = $props();
</script>

<div>
	<label for={id} class="form-label">{$t('iscsi.form.initiators')}</label>
	<textarea
		{id}
		class="form-input mono"
		rows="3"
		spellcheck="false"
		autocomplete="off"
		{placeholder}
		aria-describedby="{id}-hint"
		bind:value
	></textarea>{#if error !== undefined}{' '}{#if error}<p class="form-error">{error}</p>{/if}{/if}
	<p id="{id}-hint" class="form-hint">{$t('iscsi.form.initiatorsHint')}</p>
</div>
