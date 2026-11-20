<script lang="ts">
	import { Alert, Button, Input, Modal, Select } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import type { NewRule } from './firewall';

	// The dialog of a new rule of the host's firewall (ADR-0416): its action, protocol, port,
	// source and comment. The card sends it; the host undoes it unless it is kept.

	type Props = {
		open: boolean;
		rule: NewRule;
		/** Seconds after which the host undoes a change that is not kept. */
		revertSeconds: number;
		/** The rule is being sent: the dialog waits and cannot be closed. */
		busy: boolean;
		/** Why the rule could not be added; '' otherwise. */
		error: string;
		onadd: () => void;
		onclose: () => void;
	};

	let { open, rule = $bindable(), revertSeconds, busy, error, onadd, onclose }: Props = $props();

	const actionOptions = $derived([
		{ value: 'allow', label: $t('firewall.allow') },
		{ value: 'deny', label: $t('firewall.deny') },
		{ value: 'reject', label: $t('firewall.reject') }
	]);
	const protocolOptions = $derived([
		{ value: 'tcp', label: $t('firewall.tcp') },
		{ value: 'udp', label: $t('firewall.udp') },
		{ value: 'any', label: $t('firewall.any') }
	]);
</script>

<Modal {open} title={$t('firewall.addTitle')} closable={!busy} {onclose}>
	<form
		id="firewall-add-rule"
		class="space-y-4"
		onsubmit={(event) => {
			event.preventDefault();
			onadd();
		}}
	>
		<div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
			<Select label={$t('firewall.action')} options={actionOptions} bind:value={rule.action} />
			<Select label={$t('firewall.protocol')} options={protocolOptions} bind:value={rule.protocol} />
		</div>
		<Input label={$t('firewall.portLabel')} bind:value={rule.port} placeholder={$t('firewall.portPlaceholder')} />
		<Input label={$t('firewall.source')} bind:value={rule.source} placeholder={$t('firewall.sourcePlaceholder')} />
		<Input
			label={$t('firewall.comment')}
			bind:value={rule.comment}
			placeholder={$t('firewall.commentPlaceholder')}
			maxlength={64}
		/>
		<p class="text-sm text-content-secondary">{$t('firewall.addHint', { n: revertSeconds })}</p>
		{#if error}
			<Alert variant="error">{error}</Alert>
		{/if}
	</form>
	{#snippet footer()}
		<Button variant="secondary" type="button" disabled={busy} onclick={onclose}>{$t('common.cancel')}</Button>
		<Button variant="primary" type="submit" form="firewall-add-rule" disabled={busy}>
			{busy ? $t('firewall.adding') : $t('firewall.addRule')}
		</Button>
	{/snippet}
</Modal>
