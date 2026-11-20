<script lang="ts">
	// The Ports tab of an app's details: each container port with the host port it is published
	// on, which the install form can move; an app in the host's network binds its ports on the
	// host, which cannot be moved (ADR-0381).
	import { Badge, Input } from '$lib/components/ui';
	import type { AppTemplate } from '$lib/api/apps';
	import { t } from '$lib/i18n';

	type Props = {
		app: AppTemplate;
		/** The host port of each container port, as the form has it. */
		portValues: Record<number, string>;
		inHostNetwork: boolean;
		isInstalled: boolean;
		/** The container ports whose host port is no port number. */
		invalidPorts: number[];
	};

	let { app, portValues = $bindable(), inHostNetwork, isInstalled, invalidPorts }: Props = $props();
</script>

<div class="space-y-3">
	<p class="text-sm text-secondary mb-4">
		{inHostNetwork
			? $t('apps.detail.portsHostNetwork', { name: app.name })
			: $t('apps.detail.portsHint')}
	</p>
	{#each app.ports as port}
		<div class="card-nested">
			<div class="flex items-start gap-4">
				<Input
					class="flex-1"
					type="number"
					label={$t('apps.detail.hostPort')}
					bind:value={portValues[port.container]}
					disabled={isInstalled || inHostNetwork}
					error={invalidPorts.includes(port.container) ? $t('apps.detail.portRange') : ''}
				/>
				<span class="text-secondary pt-8">→</span>
				<Input
					class="flex-1"
					type="number"
					label={$t('apps.detail.containerPort')}
					value={String(port.container)}
					disabled
				/>
				<div class="pt-8">
					<Badge variant="info">{port.protocol.toUpperCase()}</Badge>
				</div>
			</div>
		</div>
	{/each}
</div>
