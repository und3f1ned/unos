<script lang="ts">
	import { TOKEN_AREAS } from '$lib/api/auth';
	import { Alert, Button, Checkbox, Input, Modal, RadioGroup, Select } from '$lib/components/ui';
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

		<!-- The theme's radio buttons and checkboxes (ADR-0432); the form keeps booleans. -->
		<RadioGroup
			name="api-token-access"
			legend={$t('tokens.form.access')}
			value={form.readOnly ? 'read' : 'change'}
			disabled={saving}
			options={[
				{ value: 'read', label: $t('tokens.access.read'), description: $t('tokens.form.readHint') },
				{ value: 'change', label: $t('tokens.access.change'), description: $t('tokens.form.changeHint') }
			]}
			onchange={(value) => (form.readOnly = value === 'read')}
		/>

		<div class="space-y-2 min-w-0">
			<RadioGroup
				name="api-token-areas"
				legend={$t('tokens.form.areas')}
				value={form.everyArea ? 'every' : 'some'}
				disabled={saving}
				options={[
					{ value: 'every', label: $t('tokens.areas.every'), description: $t('tokens.form.everyHint') },
					{ value: 'some', label: $t('tokens.form.someAreas') }
				]}
				onchange={(value) => (form.everyArea = value === 'every')}
			/>
			{#if !form.everyArea}
				<ul class="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1.5 pl-7" aria-label={$t('tokens.form.someAreas')}>
					{#each TOKEN_AREAS as area (area)}
						<li class="min-w-0">
							<Checkbox
								checked={form.chosen[area] === true}
								disabled={saving}
								onchange={(on) => (form.chosen = { ...form.chosen, [area]: on })}
							>
								<span class="flex flex-wrap items-center gap-x-2">
									<span>{$t(AREA_LABELS[area])}</span>
									<span class="mono text-xs text-muted break-all">{pathOf(area)}</span>
								</span>
							</Checkbox>
						</li>
					{/each}
				</ul>
				{#if form.chosenAreas.length === 0}
					<p class="form-hint">{$t('tokens.form.chooseArea')}</p>
				{/if}
			{/if}
		</div>

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
