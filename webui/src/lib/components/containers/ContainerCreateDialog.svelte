<script lang="ts">
	// The Create Container dialog of the Containers page: a name, an image, a restart policy, the
	// ports to publish and the variables to set. The page owns the form (it empties it when the
	// dialog opens), the request and its error, which the page's list shows too.
	import { Plus, Trash2 } from 'lucide-svelte';
	import { Alert, Button, Input, Modal, Select } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import type { ContainerForm } from './containerForm.svelte';

	type Props = {
		open: boolean;
		form: ContainerForm;
		/** Why the container was not created. */
		error: string;
		submitting: boolean;
		onclose: () => void;
		/** Create was asked, with Enter or the button. */
		oncreate: () => void;
	};

	let { open, form, error, submitting, onclose, oncreate }: Props = $props();
</script>

<!-- The ports or variables added, each with its remove button. -->
{#snippet added(texts: string[], remove: (index: number) => void)}
	{#if texts.length > 0}
		<div class="space-y-1">
			{#each texts as text, i}
				<div
					class="flex items-center justify-between py-1.5 px-3 bg-surface-muted rounded text-sm"
				>
					<span class="mono">{text}</span>
					<Button variant="plain"
						type="button"
						onclick={() => remove(i)}
					 class="text-error hover:text-status-error"
					>
						<Trash2 class="w-4 h-4" />
					</Button>
				</div>
			{/each}
		</div>
	{/if}
{/snippet}

<Modal {open} title={$t('containers.create.title')} size="lg" {onclose}>
	<form
		onsubmit={(e) => {
			e.preventDefault();
			oncreate();
		}}
		class="space-y-4"
	>
		<Input
			label={$t('containers.create.name')}
			bind:value={form.formData.name}
			placeholder={$t('containers.create.namePlaceholder')}
			required
		/>

		<Input
			label={$t('containers.create.image')}
			bind:value={form.formData.image}
			placeholder={$t('containers.create.imagePlaceholder')}
			hint={$t('containers.create.imageHint')}
			required
		/>

		<Select
			label={$t('containers.create.restartPolicy')}
			bind:value={form.formData.restart_policy}
			options={[
				{ value: 'no', label: $t('containers.create.restartNo') },
				{ value: 'always', label: $t('containers.create.restartAlways') },
				{ value: 'unless-stopped', label: $t('containers.create.restartUnlessStopped') },
				{ value: 'on-failure', label: $t('containers.create.restartOnFailure') }
			]}
		/>

		<!-- Port Mappings -->
		<div>
			<label class="form-label">{$t('containers.create.ports')}</label>
			<!-- The fields and Add wrap on a narrow screen (ADR-0302). -->
			<div class="flex flex-wrap gap-2 mb-2">
				<input
					type="number"
					bind:value={form.portMapping.host}
					placeholder={$t('containers.create.hostPort')}
					class="form-input w-24"
				/>
				<span class="text-secondary self-center">:</span>
				<input
					type="number"
					bind:value={form.portMapping.container}
					placeholder={$t('containers.create.containerPort')}
					class="form-input w-24"
				/>
				<select bind:value={form.portMapping.protocol} class="form-select w-24">
					<option value="tcp">{$t('containers.protocol.tcp')}</option>
					<option value="udp">{$t('containers.protocol.udp')}</option>
				</select>
				<Button variant="secondary" size="sm" onclick={() => form.addPort()} type="button"
					>{$t('containers.create.add')}</Button
				>
			</div>
			{@render added(
				form.portsList.map((port) => `${port.host}:${port.container}/${port.protocol}`),
				(i) => form.removePort(i)
			)}
		</div>

		<!-- Environment Variables -->
		<div>
			<label class="form-label">{$t('containers.create.environment')}</label>
			<div class="flex flex-wrap gap-2 mb-2">
				<input
					type="text"
					bind:value={form.envVar.key}
					placeholder={$t('containers.create.envKey')}
					class="form-input flex-1 min-w-[7rem]"
				/>
				<span class="text-secondary self-center">=</span>
				<input
					type="text"
					bind:value={form.envVar.value}
					placeholder={$t('containers.create.envValue')}
					class="form-input flex-1 min-w-[7rem]"
				/>
				<Button variant="secondary" size="sm" onclick={() => form.addEnvVar()} type="button"
					>{$t('containers.create.add')}</Button
				>
			</div>
			{@render added(
				form.envList.map((env) => `${env.key}=${env.value}`),
				(i) => form.removeEnvVar(i)
			)}
		</div>

		{#if error}
			<Alert variant="error">{error}</Alert>
		{/if}
	</form>

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" onclick={onclose}
			>{$t('common.cancel')}</Button
		>
		<Button
			variant="primary"
			class="flex-1"
			loading={submitting}
			onclick={oncreate}
			icon={Plus}
		>
			{$t('containers.create.button')}
		</Button>
	{/snippet}
</Modal>
