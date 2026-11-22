<script lang="ts">
	import { messageOf } from '$lib/errors';
	// The memory and CPU limits of one container of an app (ADR-0390): an empty field is no limit.
	// The server checks the bounds and refuses a memory limit below what a running container uses
	// now; its message shows here as it comes. Docker applies the limits at once, and UNOS keeps
	// them for the app's next install.
	import { untrack } from 'svelte';
	import { Alert, Button, Input, Modal } from '$lib/components/ui';
	import { formatNumber } from '$lib/format';
	import { intlLocale, t } from '$lib/i18n';
	import {
		getLimitBounds,
		setContainerLimits,
		type ContainerLimits,
		type ContainerResources,
		type LimitBounds
	} from '$lib/api/apps';

	type Props = {
		open?: boolean;
		/** The container's Docker name. */
		container?: string;
		/** Its limits and use as the installed list gave them. */
		resources?: ContainerResources | null;
		/** Cancel, Escape or the backdrop asked to close the dialog. */
		onclose?: () => void;
		/** The server took the limits: what it keeps now. */
		onsaved?: (limits: ContainerLimits) => void;
	};

	let { open = false, container = '', resources = null, onclose, onsaved }: Props = $props();

	const MIB = 1024 * 1024;

	let memory = $state('');
	let cpus = $state('');
	let bounds = $state<LimitBounds | null>(null);
	let error = $state('');
	let saving = $state(false);
	// The container whose limits the fields were filled with, while the dialog is open. Only the
	// effect below reads it.
	let filledFor = '';

	// The fields are filled when the dialog opens or shows another container, before the dialog's
	// markup updates, as Svelte 4's statements did; only `open` and `container` make it run again
	// (a change of `resources` while it is open leaves what the user typed).
	$effect.pre(() => {
		const isOpen = open;
		const name = container;
		untrack(() => {
			if (!isOpen) filledFor = '';
			else if (name !== filledFor) void fill();
		});
	});

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

	// A bound or a use in the language's digits, its thousands grouped (8,192 MB).
	function amount(value: number, digits: number): string {
		return formatNumber(value, $intlLocale, { digits, grouping: true });
	}

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
			onsaved?.(result);
		} catch (e) {
			error = messageOf(e);
		} finally {
			saving = false;
		}
	}
</script>

<Modal {open} title={$t('apps.limits.title', { name: container })} closable={!saving} onclose={() => onclose?.()}>
	<form
		id="app-limits-form"
		class="space-y-4"
		onsubmit={(e) => {
			e.preventDefault();
			void save();
		}}
	>
		<p class="text-sm text-secondary">{$t('apps.limits.intro')}</p>
		<Input
			type="text"
			label={$t('apps.limits.memory')}
			bind:value={memory}
			placeholder={$t('apps.limits.none')}
			hint={[
				bounds
					? $t('apps.limits.memoryHint', {
							min: amount(bounds.memory_min / MIB, 0),
							max: amount(Math.floor(bounds.memory_max / MIB), 0)
						})
					: '',
				// The use now, which a new limit must stay above.
				resources?.memory_used != null
					? $t('apps.limits.memoryNow', { used: amount(Math.ceil(resources.memory_used / MIB), 0) })
					: ''
			]
				.filter(Boolean)
				.join(' ')}
		/>
		<Input
			type="text"
			label={$t('apps.limits.cpu')}
			bind:value={cpus}
			placeholder={$t('apps.limits.none')}
			hint={bounds ? $t('apps.limits.cpuHint', { min: amount(bounds.cpu_min, 2), max: amount(bounds.cpu_max, 2) }) : ''}
		/>
		{#if error}
			<!-- Read out as it appears: the server's refusal, or a field that is no number. -->
			<div role="alert"><Alert variant="error">{error}</Alert></div>
		{/if}
	</form>
	{#snippet footer()}
		<Button variant="secondary" disabled={saving} onclick={() => onclose?.()}>
			{$t('common.cancel')}
		</Button>
		<Button variant="primary" loading={saving} onclick={save}>
			{$t('apps.limits.save')}
		</Button>
	{/snippet}
</Modal>
