<script lang="ts">
	import { Check, ChevronRight, Copy } from 'lucide-svelte';
	import { t } from '$lib/i18n';

	// Technical text, folded (ADR-0432): the server's raw message, a command, a log line, behind
	// "Technical details", with a button that copies it. The page leads with a plain sentence.
	type Props = {
		text: string;
		summary?: string;
		class?: string;
	};

	let { text, summary = '', class: className = '' }: Props = $props();
	let copied = $state(false);

	async function copy() {
		try {
			await navigator.clipboard.writeText(text);
			copied = true;
			setTimeout(() => (copied = false), 2000);
		} catch {
			// No clipboard (an insecure page, a refusal): the text can still be selected.
		}
	}
</script>

<details class="tech-details {className}">
	<summary><ChevronRight class="w-3.5 h-3.5 details-chevron" aria-hidden="true" />{summary || $t('ui.error.details')}</summary>
	<div class="tech-details-body">
		<pre>{text}</pre>
		<button type="button" class="btn btn-ghost btn-icon-sm shrink-0" aria-label={$t('ui.error.copy')} title={$t('ui.error.copy')} onclick={copy}>
			{#if copied}
				<Check class="w-4 h-4 text-status-success" aria-hidden="true" />
				<span class="sr-only" aria-live="polite">{$t('ui.error.copied')}</span>
			{:else}
				<Copy class="w-4 h-4" aria-hidden="true" />
			{/if}
		</button>
	</div>
</details>
