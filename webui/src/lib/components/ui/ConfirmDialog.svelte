<script lang="ts">
	import type { Snippet } from 'svelte';
	import { t } from '$lib/i18n';
	import Alert from './Alert.svelte';
	import Button from './Button.svelte';
	import Input from './Input.svelte';
	import Modal from './Modal.svelte';

	// The one shape of a confirmation (ADR-0432): a small dialog with the question as its title, a
	// sentence, what will happen as a list, and two buttons, Cancel and the action, whose text is a
	// verb and its object ("Delete backup"). A dangerous action's button is the one solid red
	// button of the interface, with the warning icon in the title; `warning` asks before something
	// that can be undone. With `typeToConfirm` the action waits for its text typed into an empty
	// field, the text shown above it. `confirm()` of ./confirm asks with it from a script.
	type Props = {
		open: boolean;
		title: string;
		message?: string;
		/** What will happen, each a short sentence. */
		consequences?: string[];
		/** The action's button: a verb and its object. */
		confirmLabel: string;
		cancelLabel?: string;
		tone?: 'danger' | 'warning';
		/** A text, such as the object's name, that must be typed before the action. */
		typeToConfirm?: string;
		/** The action runs: the buttons wait, and the dialog stays open. */
		busy?: boolean;
		/** Why the action failed, shown in the dialog. */
		error?: string;
		onconfirm: () => void;
		oncancel: () => void;
		children?: Snippet;
	};

	let {
		open,
		title,
		message = '',
		consequences = [],
		confirmLabel,
		cancelLabel = '',
		tone = 'danger',
		typeToConfirm = '',
		busy = false,
		error = '',
		onconfirm,
		oncancel,
		children
	}: Props = $props();

	let typed = $state('');
	const matches = $derived(!typeToConfirm || typed.trim() === typeToConfirm);
	const fieldId = `confirm-type-${Math.random().toString(36).slice(2, 9)}`;
	const formId = `${fieldId}-form`;

	// A dialog asked again starts with an empty field.
	$effect.pre(() => {
		if (!open) typed = '';
	});

	function submit(event: SubmitEvent) {
		event.preventDefault();
		if (busy || !matches) return;
		onconfirm();
	}
</script>

<Modal {open} {title} size="sm" {tone} closable={!busy} onclose={oncancel}>
	<form id={formId} class="space-y-4" onsubmit={submit}>
		{#if message}
			<p class="text-content-soft">{message}</p>
		{/if}
		{#if consequences.length}
			<div>
				<p class="eyebrow mb-1.5">{$t('ui.confirm.consequences')}</p>
				<ul class="confirm-consequences">
					{#each consequences as consequence, index (index)}
						<li>{consequence}</li>
					{/each}
				</ul>
			</div>
		{/if}
		{@render children?.()}
		{#if typeToConfirm}
			<div>
				<!-- The label names the text to type, so that a screen reader hears it with the field. -->
				<label for={fieldId} class="form-label">
					{$t('ui.confirm.typeLabel')} <code class="confirm-expected">{typeToConfirm}</code>
				</label>
				<Input
					id={fieldId}
					bind:value={typed}
					autocomplete="off"
					spellcheck={false}
					selectOnMount
					hint={$t('ui.confirm.typeHint')}
				/>
			</div>
		{/if}
		{#if error}
			<Alert variant="error" size="sm">{error}</Alert>
		{/if}
	</form>
	{#snippet footer()}
		<Button variant="secondary" disabled={busy} onclick={oncancel}>{cancelLabel || $t('common.cancel')}</Button>
		<Button
			type="submit"
			form={formId}
			variant={tone === 'danger' ? 'danger' : 'primary'}
			loading={busy}
			disabled={!matches}
			disabledReason={matches ? '' : $t('ui.confirm.typeMismatch')}
		>
			{confirmLabel}
		</Button>
	{/snippet}
</Modal>
