<script lang="ts">
	import type { IscsiSecrets } from '$lib/api/iscsi';
	import { Alert, Button, Modal, toast } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	// The secrets of a target that was created or got new ones, shown once. Mounted while open.
	type Props = {
		secrets: IscsiSecrets;
		/** Escape, the backdrop or Done. */
		onclose: () => void;
	};

	let { secrets, onclose }: Props = $props();

	// A failed copy stays under the secrets, which are then to be copied by hand.
	let copyFailed = $state(false);

	async function copy(text: string | null) {
		if (!text) return;
		try {
			await navigator.clipboard.writeText(text);
			copyFailed = false;
			toast.success($t('iscsi.secret.copied'));
		} catch {
			copyFailed = true;
		}
	}
</script>

<!-- A secret in a read-only field, with its copy button. -->
{#snippet secretField(id: string, label: string, value: string)}
	<div>
		<label for={id} class="form-label">{label}</label>
		<div class="flex flex-wrap items-center gap-2">
			<input {id} class="form-input mono flex-1 min-w-0" readonly {value} />
			<Button variant="secondary" size="sm" onclick={() => copy(value)}>
				{$t('iscsi.secret.copy')}
			</Button>
		</div>
	</div>
{/snippet}

<Modal open title={$t('iscsi.secret.title', { name: secrets.target.name })} size="md" {onclose}>
	<div class="space-y-4">
		<Alert variant="warning">{$t('iscsi.secret.once')}</Alert>
		<dl class="grid grid-cols-1 gap-y-1 text-sm">
			<dt class="text-content-secondary">{$t('iscsi.iqn')}</dt>
			<dd class="mono break-all">{secrets.target.iqn}</dd>
			<dt class="text-content-secondary">{$t('iscsi.chapUser')}</dt>
			<dd class="mono break-all">{secrets.target.chap_user}</dd>
		</dl>
		{#if secrets.chap_secret}
			{@render secretField('iscsi-shown-secret', $t('iscsi.secret.chap'), secrets.chap_secret)}
		{/if}
		{#if secrets.mutual_secret}
			{@render secretField('iscsi-shown-mutual', $t('iscsi.secret.mutual'), secrets.mutual_secret)}
		{/if}
		{#if copyFailed}<p class="text-sm text-status-error">{$t('iscsi.secret.copyFailed')}</p>{/if}
	</div>
	{#snippet footer()}
		<Button class="flex-1" onclick={() => onclose()}>{$t('iscsi.secret.done')}</Button>
	{/snippet}
</Modal>
