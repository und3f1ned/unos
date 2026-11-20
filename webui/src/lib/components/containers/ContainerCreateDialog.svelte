<script lang="ts">
	// The Create Container dialog of the Containers page: a name, an image, a restart policy, the
	// ports to publish and the variables to set. The page owns the form (it empties it when the
	// dialog opens), the request and its error, which the page's list shows too.
	import { untrack } from 'svelte';
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

	// Create was asked once: an empty name or image says so under its field (webui.ux-apps), and
	// Create stays enabled. Opened again, the form starts without them.
	let tried = $state(false);
	$effect.pre(() => {
		const isOpen = open;
		untrack(() => {
			if (isOpen) tried = false;
		});
	});

	function submit() {
		tried = true;
		oncreate();
	}
</script>

<!-- The ports or variables added, each with its remove button. -->
{#snippet added(texts: string[], remove: (index: number) => void, removeKey: string)}
	{#if texts.length > 0}
		<ul class="space-y-1 mt-2">
			{#each texts as text, i (i)}
				<li class="flex items-center justify-between gap-2 py-1 pl-3 pr-1 bg-surface-muted rounded text-sm">
					<span class="mono break-all">{text}</span>
					<Button variant="ghost-danger" size="icon-sm" onclick={() => remove(i)} title={$t(removeKey, { name: text })}>
						<Trash2 class="w-4 h-4" aria-hidden="true" />
						<span class="sr-only">{$t(removeKey, { name: text })}</span>
					</Button>
				</li>
			{/each}
		</ul>
	{/if}
{/snippet}

<Modal {open} title={$t('containers.create.title')} size="lg" {onclose}>
	<!-- Enter in a field submits the form through Create, its submit button in the footer; novalidate:
	     the dialog says under each field what is missing, instead of the browser's bubble. -->
	<form
		id="container-create-form"
		novalidate
		onsubmit={(e) => {
			e.preventDefault();
			submit();
		}}
		class="space-y-4"
	>
		<Input
			label={$t('containers.create.name')}
			bind:value={form.formData.name}
			placeholder={$t('containers.create.namePlaceholder')}
			error={tried && !form.formData.name.trim() ? $t('containers.create.nameRequired') : ''}
			required
		/>

		<Input
			label={$t('containers.create.image')}
			bind:value={form.formData.image}
			placeholder={$t('containers.create.imagePlaceholder')}
			hint={$t('containers.create.imageHint')}
			error={tried && !form.formData.image.trim() ? $t('containers.create.imageRequired') : ''}
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

		<!-- Ports: each field with its own label, in one row from sm, two per row on a phone
		     (webui.ux-apps). -->
		<fieldset>
			<legend class="form-label">{$t('containers.create.ports')}</legend>
			<div class="grid grid-cols-2 sm:grid-cols-[1fr_1fr_7rem_auto] items-end gap-2">
				<Input
					type="number"
					label={$t('containers.create.hostPort')}
					bind:value={form.portMapping.host}
					placeholder={$t('containers.create.hostPortExample')}
				/>
				<Input
					type="number"
					label={$t('containers.create.containerPort')}
					bind:value={form.portMapping.container}
					placeholder={$t('containers.create.containerPortExample')}
				/>
				<Select
					label={$t('containers.create.protocol')}
					bind:value={form.portMapping.protocol}
					options={[
						{ value: 'tcp', label: $t('containers.protocol.tcp') },
						{ value: 'udp', label: $t('containers.protocol.udp') }
					]}
				/>
				<Button variant="secondary" icon={Plus} onclick={() => form.addPort()} type="button">
					{$t('containers.create.addPort')}
				</Button>
			</div>
			{@render added(
				form.portsList.map((port) => `${port.host}:${port.container}/${port.protocol}`),
				(i) => form.removePort(i),
				'containers.create.removeItem'
			)}
		</fieldset>

		<!-- Variables: a name and a value, then Add. -->
		<fieldset>
			<legend class="form-label">{$t('containers.create.environment')}</legend>
			<div class="grid grid-cols-2 sm:grid-cols-[1fr_1fr_auto] items-end gap-2">
				<Input
					label={$t('containers.create.envKey')}
					bind:value={form.envVar.key}
					placeholder={$t('containers.create.envKeyExample')}
					spellcheck={false}
				/>
				<Input
					label={$t('containers.create.envValue')}
					bind:value={form.envVar.value}
					placeholder={$t('containers.create.envValueExample')}
					spellcheck={false}
				/>
				<Button variant="secondary" icon={Plus} onclick={() => form.addEnvVar()} type="button" class="col-span-2 sm:col-span-1 justify-self-start">
					{$t('containers.create.addVariable')}
				</Button>
			</div>
			{@render added(
				form.envList.map((env) => `${env.key}=${env.value}`),
				(i) => form.removeEnvVar(i),
				'containers.create.removeItem'
			)}
		</fieldset>

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
			type="submit"
			form="container-create-form"
			icon={Plus}
		>
			{$t('containers.create.button')}
		</Button>
	{/snippet}
</Modal>
