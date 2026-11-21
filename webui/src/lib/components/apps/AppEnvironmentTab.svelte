<script lang="ts">
	// The Environment tab of an app's details: each container's variables. The install form edits
	// the main container's own; UNOS sets PUID and PGID, which the install option decides
	// (ADR-0274), the wiring between the app's containers and every variable of a companion
	// (ADR-0381).
	import { Input } from '$lib/components/ui';
	import { isSetByUnos, type AppContainer, type AppTemplate } from '$lib/api/apps';
	import { t } from '$lib/i18n';
	import AppContainerGroup from './AppContainerGroup.svelte';
	import { setByUnosHint, setByUnosValue } from './detail';

	type Props = {
		app: AppTemplate;
		/** The app's containers, its main container first. */
		containers: AppContainer[];
		several: boolean;
		isInstalled: boolean;
		/** The app runs (installed) or is to run (the form) in the group unos-smb. */
		inUnosSmb: boolean;
		/** The value of each variable, as the form has it. */
		envValues: Record<string, string>;
		/** The required variables left empty. */
		missingRequired: string[];
	};

	let { app, containers, several, isInstalled, inUnosSmb, envValues = $bindable(), missingRequired }: Props = $props();
</script>

<div class="space-y-3">
	<p class="text-sm text-secondary mb-4">{$t('apps.detail.environmentHint')}</p>
	{#each containers as container (container.name)}
		{#if container.spec.environment.length > 0}
			<AppContainerGroup name={container.name} kind="environment" {several}>
				{#each container.spec.environment as env}
					<div class="card-nested">
						{#if container.companion !== null}
							<!-- A companion takes no override: UNOS sets all its variables (ADR-0381). -->
							<Input
								class="font-mono text-sm"
								label={env.name}
								value={env.value}
								disabled
								hint={$t('apps.detail.setByUnos')}
							/>
						{:else if isSetByUnos(env)}
							<!-- The server sets PUID and PGID, which the install option decides (ADR-0274), and
							     the wiring between the app's containers (ADR-0381). -->
							<Input
								class="font-mono text-sm"
								label={env.name}
								value={setByUnosValue(env, inUnosSmb)}
								disabled
								hint={setByUnosHint(app, env, isInstalled, inUnosSmb, $t)}
							/>
						{:else}
							<Input
								class="font-mono text-sm"
								label={env.name}
								type={env.secret ? 'password' : 'text'}
								bind:value={envValues[env.name]}
								required={env.required}
								disabled={isInstalled}
								hint={env.description}
								placeholder={env.secret
									? $t(env.required ? 'apps.detail.secretPlaceholder' : 'apps.detail.optional')
									: $t('apps.detail.enter', { name: env.name })}
								autocomplete={env.secret ? 'new-password' : 'off'}
								error={missingRequired.includes(env.name) ? $t('apps.detail.required') : ''}
							/>
						{/if}
					</div>
				{/each}
			</AppContainerGroup>
		{/if}
	{/each}
</div>
