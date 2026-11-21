<script lang="ts">
	import { untrack } from 'svelte';
	import type { UpdateConfig } from '$lib/api/updates';
	import { Button, Card, Input, SwitchRow } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';

	// The automatic install (ADR-0443): on, UNOS looks for a newer stable release at the interval and
	// installs it as Install does. The next look is the server's, on the NAS's clock.
	type Props = {
		config: UpdateConfig;
		nextCheck: string | null;
		zone: string | null;
		saving?: boolean;
		onsave: (config: UpdateConfig) => void;
	};

	let { config, nextCheck, zone, saving = false, onsave }: Props = $props();

	// The form's own copy, taken again when the stored settings change.
	let on = $state(false);
	let hours = $state('24');
	$effect.pre(() => {
		const stored = config;
		untrack(() => {
			on = stored.auto_install;
			hours = String(stored.check_interval_hours);
		});
	});

	let interval = $derived(Number(hours));
	let valid = $derived(Number.isInteger(interval) && interval >= 1 && interval <= 168);
	let changed = $derived(on !== config.auto_install || (on && interval !== config.check_interval_hours));
</script>

<Card title={$t('updates.auto.title')}>
	<form
		class="space-y-4 content-narrow"
		onsubmit={(e) => {
			e.preventDefault();
			onsave({ auto_install: on, check_interval_hours: on ? interval : config.check_interval_hours });
		}}
	>
		<SwitchRow title={$t('updates.auto.switch')} help={$t('updates.auto.help')} bind:checked={on}>
			<Input
				type="number"
				label={$t('updates.auto.interval')}
				bind:value={hours}
				suffix={$t('updates.auto.hours')}
				min={1}
				max={168}
				width="xs"
			/>
		</SwitchRow>
		{#if config.auto_install && nextCheck}
			<p class="text-sm text-content-secondary">
				{$t('updates.auto.next', {
					date: formatDateTime(nextCheck, $intlLocale, { dateStyle: 'medium', timeStyle: 'short', zone })
				})}
			</p>
		{/if}
		<Button
			type="submit"
			variant="secondary"
			loading={saving}
			disabled={!changed || !valid}
			disabledReason={!changed ? $t('updates.auto.noChanges') : $t('updates.auto.invalid')}
		>
			{$t('updates.auto.save')}
		</Button>
	</form>
</Card>
