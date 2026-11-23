<script lang="ts">
	import { setHostname } from '$lib/api/network';
	import { setTimezone } from '$lib/api/system';
	import { setNasTimeZone } from '$lib/stores/nasTime';
	import { Alert, Button, Card, ErrorState, HelpText, Input, Select, toast } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import type { HostSettings } from './host.svelte';

	// The host's name and time zone, each saved on its own; what the host answered.
	type Props = {
		host: HostSettings;
	};

	let { host }: Props = $props();

	let savingHostname = $state(false);
	let savingZone = $state(false);
	let hostError = $state('');

	async function saveHostname() {
		const name = host.hostname.trim();
		savingHostname = true;
		hostError = '';
		try {
			const answer = await setHostname(name);
			host.hostname = host.savedHostname = answer.hostname;
			toast.success($t('general.host.hostnameSaved', { name: answer.hostname }));
		} catch (e) {
			hostError = e instanceof Error ? e.message : $t('general.host.saveFailed');
		} finally {
			savingHostname = false;
		}
	}

	async function saveZone() {
		savingZone = true;
		hostError = '';
		try {
			const answer = await setTimezone(host.zone);
			host.zone = host.savedZone = answer.timezone;
			setNasTimeZone(answer.timezone);
			toast.success($t('general.host.zoneSaved', { zone: answer.timezone }));
		} catch (e) {
			hostError = e instanceof Error ? e.message : $t('general.host.saveFailed');
			host.zone = host.savedZone; // the host kept its zone
		} finally {
			savingZone = false;
		}
	}
</script>

<!-- The name and time zone of the host, set on the host (ADR-0331) -->
<Card title={$t('general.host.title')}>
	{#if host.loadFailed}
		<ErrorState title={$t('general.host.loadFailed')} onretry={() => host.load()} />
	{:else}
		<div class="space-y-4">
			{#if host.hostnameLoaded}
				<div>
					<form
						class="flex flex-wrap items-end gap-3"
						onsubmit={(e) => {
							e.preventDefault();
							saveHostname();
						}}
					>
						<div class="min-w-0 flex-1 basis-56">
							<Input
								id="general-hostname"
								label={$t('general.host.hostname')}
								bind:value={host.hostname}
								maxlength={63}
							/>
						</div>
						<!-- Disabled only while there is nothing to save, and it says so. -->
						<Button
							type="submit"
							variant="secondary"
							loading={savingHostname}
							disabled={!host.hostname.trim() || host.hostname.trim() === host.savedHostname}
							disabledReason={!host.hostname.trim() || host.hostname.trim() === host.savedHostname
								? $t('general.host.unchanged')
								: ''}
						>
							{$t('general.host.saveHostname')}
						</Button>
					</form>
					<HelpText text={$t('general.host.hostnameHint')} class="mt-1.5" />
				</div>
			{/if}
			{#if host.zoneLoaded}
				<div>
					<div class="flex flex-wrap items-end gap-3">
						<div class="min-w-0 flex-1 basis-56">
							<Select
								id="general-zone"
								label={$t('general.host.zone')}
								bind:value={host.zone}
								options={host.zoneOptions}
								placeholder={$t('general.host.unknownZone')}
								disabled={host.zones.length === 0}
							/>
						</div>
						<Button
							variant="secondary"
							loading={savingZone}
							disabled={!host.zone || host.zone === host.savedZone}
							disabledReason={!host.zone || host.zone === host.savedZone ? $t('general.host.unchanged') : ''}
							onclick={saveZone}
						>
							{$t('general.host.saveZone')}
						</Button>
					</div>
					<HelpText
						text={host.zones.length === 0 ? $t('general.host.noZones') : $t('general.host.zoneHint')}
						class="mt-1.5"
					/>
				</div>
			{/if}
			<div role="alert">
				{#if hostError}
					<Alert variant="error" dismissible ondismiss={() => (hostError = '')}>{hostError}</Alert>
				{/if}
			</div>
		</div>
	{/if}
</Card>
