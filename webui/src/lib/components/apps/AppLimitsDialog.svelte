<script lang="ts">
	// The memory and CPU limits of one container of an app (ADR-0388): an empty field is no limit.
	// The server checks the bounds and refuses a memory limit below what a running container uses
	// now; its message shows here as it comes. Docker applies the limits at once, and UNOS keeps
	// them for the app's next install.
	import { createEventDispatcher } from 'svelte';
	import { Alert, Button, Input, Modal } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import {
		getLimitBounds,
		setContainerLimits,
		type ContainerLimits,
		type ContainerResources,
		type LimitBounds
	} from '$lib/api/apps';

	export let open = false;
	/** The container's Docker name. */
	export let container = '';
	/** Its limits and use as the installed list gave them. */
	export let resources: ContainerResources | null = null;

	const dispatch = createEventDispatcher<{ close: void; saved: ContainerLimits }>();
	const MIB = 1024 * 1024;

	let memory = '';
	let cpus = '';
	let bounds: LimitBounds | null = null;
	let error = '';
	let saving = false;
	// The container whose limits the fields were filled with, while the dialog is open.
	let filledFor = '';

	$: if (open && container !== filledFor) void fill();
	$: if (!open) filledFor = '';

	async function fill() {
		filledFor = container;
		memory = resources?.memory_limit != null ? String(Math.round(resources.memory_limit / MIB)) : '';
		cpus = resources?.cpu_limit != null ? String(resources.cpu_limit) : '';
		error = '';
		try {
			bounds = await getLimitBounds();
		} catch {
			// The hints wait for the bounds; the server checks them anyway.
			bounds = null;
		}
	}

	$: formatNumber = (value: number, digits: number): string =>
		new Intl.NumberFormat($intlLocale, { maximumFractionDigits: digits }).format(value);

	// Text fields, not number fields: a browser's number field gives "" for what it cannot read
	// ("1,5" in an English one, "512MB"), which would remove the limit; here it is refused.
	/** A field's number, with a comma or a point; null for an empty field, NaN for anything else. */
	function parse(text: string): number | null {
		const trimmed = text.trim().replace(',', '.');
		if (trimmed === '') return null;
		return /^\d+(\.\d+)?$/.test(trimmed) ? Number(trimmed) : NaN;
	}

	async function save() {
		const memoryMib = parse(memory);
		const cpuLimit = parse(cpus);
		if (Number.isNaN(memoryMib) || Number.isNaN(cpuLimit)) {
			error = $t('apps.limits.notANumber');
			return;
		}
		saving = true;
		error = '';
		try {
			const result = await setContainerLimits(container, {
				memory_limit: memoryMib === null ? null : Math.round(memoryMib * MIB),
				cpu_limit: cpuLimit
			});
			dispatch('saved', result);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			saving = false;
		}
	}
</script>

<Modal
	{open}
	title={$t('apps.limits.title', { name: container })}
	closable={!saving}
	on:close={() => dispatch('close')}
>
	<form id="app-limits-form" class="space-y-4" on:submit|preventDefault={save}>
		<p class="text-sm text-secondary">{$t('apps.limits.intro')}</p>
		<Input
			type="text"
			label={$t('apps.limits.memory')}
			bind:value={memory}
			placeholder={$t('apps.limits.none')}
			hint={bounds
				? $t('apps.limits.memoryHint', {
						min: formatNumber(bounds.memory_min / MIB, 0),
						max: formatNumber(Math.floor(bounds.memory_max / MIB), 0)
					})
				: ''}
		/>
		{#if resources?.memory_used != null}
			<p class="text-xs text-muted">
				{$t('apps.limits.memoryNow', { used: formatNumber(Math.ceil(resources.memory_used / MIB), 0) })}
			</p>
		{/if}
		<Input
			type="text"
			label={$t('apps.limits.cpu')}
			bind:value={cpus}
			placeholder={$t('apps.limits.none')}
			hint={bounds
				? $t('apps.limits.cpuHint', { min: formatNumber(bounds.cpu_min, 2), max: formatNumber(bounds.cpu_max, 2) })
				: ''}
		/>
		{#if error}
			<!-- Read out as it appears: the server's refusal, or a field that is no number. -->
			<div role="alert"><Alert variant="error">{error}</Alert></div>
		{/if}
	</form>
	<svelte:fragment slot="footer">
		<Button variant="secondary" disabled={saving} on:click={() => dispatch('close')}>
			{$t('common.cancel')}
		</Button>
		<Button variant="primary" loading={saving} on:click={save}>
			{$t('apps.limits.save')}
		</Button>
	</svelte:fragment>
</Modal>
