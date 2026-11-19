<script lang="ts">
	import { Alert, Button, Modal } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	// Asks before the firewall is turned on (ADR-0416): what UNOS lets in first, that the host
	// undoes the change unless it is kept, and a warning for a browser outside the local networks.

	type Props = {
		open: boolean;
		/** The local networks that UNOS's rules let in. */
		networks: string[];
		/** Seconds after which the host undoes a change that is not kept. */
		revertSeconds: number;
		/** This browser reaches the NAS from outside the local networks. */
		notLocal: boolean;
		busy: boolean;
		onconfirm: () => void;
		onclose: () => void;
	};

	let { open, networks, revertSeconds, notLocal, busy, onconfirm, onclose }: Props = $props();
</script>

<Modal {open} title={$t('firewall.enableTitle')} {onclose}>
	<div class="space-y-3 text-sm break-words">
		<p>{$t('firewall.enableRules', { networks: networks.join(', ') })}</p>
		<p>{$t('firewall.enableRevert', { n: revertSeconds })}</p>
		{#if notLocal}
			<Alert variant="warning">{$t('firewall.notLocal')}</Alert>
		{/if}
		<p>{$t('firewall.enableOthers')}</p>
		<p>{$t('firewall.docker')}</p>
	</div>
	{#snippet footer()}
		<Button variant="secondary" onclick={onclose}>{$t('common.cancel')}</Button>
		<Button variant="primary" disabled={busy} onclick={onconfirm}>{$t('firewall.turnOn')}</Button>
	{/snippet}
</Modal>
