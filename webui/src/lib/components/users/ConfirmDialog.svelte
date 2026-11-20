<script lang="ts">
	import { Alert, Button, Modal } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	type Props = {
		open: boolean;
		title: string;
		danger?: boolean;
		// The action is on its way: the dialog stays open, its buttons wait.
		busy: boolean;
		// The question, in parts around the name of what it is about (ADR-0296); while the name is
		// null, the dialog asks nothing.
		before: string;
		name: string | null;
		after: string;
		// What the action does, one line each, under the question.
		consequences?: string[];
		// The confirming button's text.
		submit: string;
		// The server's refusal, under its title.
		error?: string;
		errorTitle?: string;
		// Cancel, Escape, the backdrop or the close button.
		onclose: () => void;
		onconfirm: () => void;
	};

	let {
		open,
		title,
		danger = false,
		busy,
		before,
		name,
		after,
		consequences = [],
		submit,
		error = '',
		errorTitle = '',
		onclose,
		onconfirm
	}: Props = $props();
</script>

<!-- A question about one user or group, answered with Cancel or the action. -->
<Modal {open} {title} size="md" {danger} closable={!busy} {onclose}>
	{#if name !== null}
		<!-- In parts around the name: ADR-0296. -->
		<p>
			{before}
			<strong class="text-content">{name}</strong>{after}
		</p>
		{#if consequences.length > 0}
			<ul class="list-disc space-y-1 pl-5 text-sm text-content-secondary">
				{#each consequences as consequence (consequence)}
					<li>{consequence}</li>
				{/each}
			</ul>
		{/if}
		{#if error}
			<Alert variant="error" title={errorTitle}>{error}</Alert>
		{/if}
	{/if}

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={busy} onclick={() => onclose()}>
			{$t('common.cancel')}
		</Button>
		<Button variant="danger" class="flex-1" loading={busy} onclick={() => onconfirm()}>
			{submit}
		</Button>
	{/snippet}
</Modal>
