<script lang="ts">
	// The Overview tab of a container's drawer: its id (with a copy button), status, creation,
	// image and labels.
	import { Check, Copy, Tag } from 'lucide-svelte';
	import { Badge, Button } from '$lib/components/ui';
	import type { Container } from '$lib/api/containers';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';
	import { labelOf, STATES } from './labels';

	type Props = {
		container: Container;
		/** The id was just copied: the button shows a check. */
		copied: boolean;
		oncopy: () => void;
	};

	let { container, copied, oncopy }: Props = $props();

	// It reads $intlLocale, so that an open drawer follows a change of the language.
	function formatDate(dateStr: string): string {
		return formatDateTime(dateStr, $intlLocale);
	}
</script>

<div class="space-y-5">
	<!-- Container ID -->
	<div class="card-nested p-4">
		<div class="flex items-center justify-between mb-2">
			<span class="text-secondary text-sm font-medium">{$t('containers.drawer.id')}</span>
			<Button variant="ghost" size="icon-sm" class="text-secondary"
				onclick={oncopy}
				title={$t('containers.drawer.copyId')}
			>
				{#if copied}
					<Check class="w-4 h-4 text-success" />
				{:else}
					<Copy class="w-4 h-4" />
				{/if}
			</Button>
		</div>
		<code class="mono-xs text-content break-all select-all">{container.id}</code>
	</div>

	<!-- Status & Created -->
	<div class="grid grid-cols-2 gap-4">
		<div class="card-nested p-4">
			<div class="text-secondary text-sm font-medium mb-2">{$t('containers.drawer.status')}</div>
			<div class="font-medium text-lg">{labelOf(STATES, container.status, $t)}</div>
		</div>
		<div class="card-nested p-4">
			<div class="text-secondary text-sm font-medium mb-2">{$t('containers.drawer.created')}</div>
			<div class="font-medium">{formatDate(container.created)}</div>
		</div>
	</div>

	<!-- Image -->
	<div class="card-nested p-4">
		<div class="text-secondary text-sm font-medium mb-2">{$t('containers.drawer.image')}</div>
		<code class="mono text-content break-all">{container.image}</code>
	</div>

	<!-- Labels -->
	{#if Object.keys(container.labels || {}).length > 0}
		<div class="card-nested p-4">
			<div class="flex items-center gap-2 mb-4">
				<Tag class="w-4 h-4 text-secondary" />
				<span class="text-secondary text-sm font-medium">{$t('containers.drawer.labels')}</span>
				<Badge variant="neutral">{Object.keys(container.labels).length}</Badge>
			</div>
			<div class="space-y-2 max-h-64 overflow-y-auto scrollbar-thin">
				{#each Object.entries(container.labels) as [key, value]}
					<div class="bg-surface-overlay rounded-lg p-3">
						<div class="text-xs text-secondary font-mono mb-1 break-all">{key}</div>
						<div class="text-sm text-content font-mono break-all">{value}</div>
					</div>
				{/each}
			</div>
		</div>
	{/if}
</div>
