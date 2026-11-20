<script lang="ts">
	import type { CreatedApiToken } from '$lib/api/auth';
	import { Alert, Button, Modal } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { accessOf, areaNames } from './apiTokens';

	// The token just made, shown only now (ADR-0239): what it may do, the token itself and Copy.

	type Props = {
		/** The token just made; the dialog is closed without one. */
		token: CreatedApiToken | null;
		copied: boolean;
		copyFailed: boolean;
		oncopy: () => void;
		/** Done: the token is never shown again. */
		ondone: () => void;
	};

	let { token, copied, copyFailed, oncopy, ondone }: Props = $props();
</script>

<!-- Closed only by Done: the token is shown only now. -->
<Modal open={token !== null} title={$t('tokens.shown.title')} size="md" closable={false}>
	{#if token}
		<div class="space-y-3">
			<Alert variant="warning">{$t('tokens.shown.save')}</Alert>
			<p class="text-sm break-words">
				{$t('tokens.shown.summary', {
					name: token.name,
					access: accessOf(token, $t),
					areas:
						token.areas === null
							? $t('tokens.areas.every')
							: $t('tokens.areas.some', { areas: areaNames(token.areas, $t) })
				})}
			</p>
			<div>
				<label for="new-api-token-value" class="form-label">{$t('tokens.shown.token')}</label>
				<textarea
					id="new-api-token-value"
					class="form-input mono break-all resize-none"
					rows="2"
					readonly
					value={token.token}
				></textarea>
			</div>
			<div class="flex flex-wrap items-center gap-2">
				<Button variant="secondary" size="sm" onclick={oncopy}>{$t('tokens.shown.copy')}</Button>
				{#if copied}<span class="text-sm text-success">{$t('tokens.shown.copied')}</span>{/if}
			</div>
			{#if copyFailed}
				<Alert variant="error">{$t('tokens.shown.copyFailed')}</Alert>
			{/if}
		</div>
	{/if}
	{#snippet footer()}
		<Button class="flex-1" onclick={ondone}>{$t('common.done')}</Button>
	{/snippet}
</Modal>
