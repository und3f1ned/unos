<script lang="ts">
	import { TOKEN_AREAS } from '$lib/api/auth';
	import { Alert, Button, Input, Modal, Select } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import type { ApiTokenForm } from './apiTokenForm.svelte';
	import { AREA_LABELS, pathOf } from './apiTokens';

	// The dialog of a new API token (ADR-0392): its name, when it expires, read or change access,
	// and every area or chosen ones. The card owns the form and sends it.

	type Props = {
		open: boolean;
		form: ApiTokenForm;
		/** The token is being made: the dialog waits and cannot be closed. */
		saving: boolean;
		/** Why the token could not be made; '' otherwise. */
		error: string;
		oncreate: () => void;
		onclose: () => void;
	};

	let { open, form, saving, error, oncreate, onclose }: Props = $props();

	// The lifetimes the dialog offers, in days; '' never expires.
	const LIFETIMES = [30, 90, 365];
	const NAME_CHARS = 64;

	const lifetimeOptions = $derived([
		{ value: '', label: $t('tokens.form.never') },
		...LIFETIMES.map((days) => ({ value: String(days), label: $t('tokens.form.afterDays', { n: days }) }))
	]);
</script>

<Modal {open} title={$t('tokens.form.title')} size="lg" closable={!saving} {onclose}>
	<form
		id="new-api-token"
		class="space-y-4"
		onsubmit={(event) => {
			event.preventDefault();
			oncreate();
		}}
	>
		<Input
			id="api-token-name"
			label={$t('tokens.form.name')}
			hint={$t('tokens.form.nameHint')}
			bind:value={form.name}
			maxlength={NAME_CHARS}
			required
			disabled={saving}
		/>
		<Select id="api-token-lifetime" label={$t('tokens.form.expiry')} bind:value={form.lifetime} options={lifetimeOptions} />

		<fieldset class="space-y-2 min-w-0">
			<legend class="form-label">{$t('tokens.form.access')}</legend>
			<label class="flex items-start gap-2 cursor-pointer">
				<input
					type="radio"
					name="api-token-access"
					class="mt-1 border-border bg-surface-overlay text-brand focus:ring-brand-ring"
					value={true}
					bind:group={form.readOnly}
					disabled={saving}
				/>
				<span>
					<span class="block">{$t('tokens.access.read')}</span>
					<span class="block text-sm text-muted">{$t('tokens.form.readHint')}</span>
				</span>
			</label>
			<label class="flex items-start gap-2 cursor-pointer">
				<input
					type="radio"
					name="api-token-access"
					class="mt-1 border-border bg-surface-overlay text-brand focus:ring-brand-ring"
					value={false}
					bind:group={form.readOnly}
					disabled={saving}
				/>
				<span>
					<span class="block">{$t('tokens.access.change')}</span>
					<span class="block text-sm text-muted">{$t('tokens.form.changeHint')}</span>
				</span>
			</label>
		</fieldset>

		<fieldset class="space-y-2 min-w-0">
			<legend class="form-label">{$t('tokens.form.areas')}</legend>
			<label class="flex items-start gap-2 cursor-pointer">
				<input
					type="radio"
					name="api-token-areas"
					class="mt-1 border-border bg-surface-overlay text-brand focus:ring-brand-ring"
					value={true}
					bind:group={form.everyArea}
					disabled={saving}
				/>
				<span>
					<span class="block">{$t('tokens.areas.every')}</span>
					<span class="block text-sm text-muted">{$t('tokens.form.everyHint')}</span>
				</span>
			</label>
			<label class="flex items-start gap-2 cursor-pointer">
				<input
					type="radio"
					name="api-token-areas"
					class="mt-1 border-border bg-surface-overlay text-brand focus:ring-brand-ring"
					value={false}
					bind:group={form.everyArea}
					disabled={saving}
				/>
				<span>{$t('tokens.form.someAreas')}</span>
			</label>
			{#if !form.everyArea}
				<ul class="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1 pl-6">
					{#each TOKEN_AREAS as area (area)}
						<li class="min-w-0">
							<label class="flex flex-wrap items-center gap-x-2 cursor-pointer">
								<input
									type="checkbox"
									class="form-checkbox"
									checked={form.chosen[area] === true}
									disabled={saving}
									onchange={(event) => (form.chosen = { ...form.chosen, [area]: event.currentTarget.checked })}
								/>
								<span>{$t(AREA_LABELS[area])}</span>
								<span class="mono text-xs text-muted break-all">{pathOf(area)}</span>
							</label>
						</li>
					{/each}
				</ul>
				{#if form.chosenAreas.length === 0}
					<p class="form-hint">{$t('tokens.form.chooseArea')}</p>
				{/if}
			{/if}
		</fieldset>

		{#if form.readOnly || !form.everyArea}
			<p class="form-hint">{$t('tokens.form.limitedNote')}</p>
		{/if}
		{#if form.warned}
			<Alert variant="warning">{$t('tokens.form.warnedAreas')}</Alert>
		{/if}
		{#if error}
			<Alert variant="error" title={$t('tokens.form.failed')}>{error}</Alert>
		{/if}
	</form>

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={saving} onclick={onclose}>
			{$t('common.cancel')}
		</Button>
		<Button class="flex-1" loading={saving} disabled={!form.canCreate} onclick={oncreate}>
			{$t('tokens.form.create')}
		</Button>
	{/snippet}
</Modal>
