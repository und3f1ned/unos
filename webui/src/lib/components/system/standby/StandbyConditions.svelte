<script lang="ts">
	import type { StandbyCondition } from '$lib/api/standby';
	import { Badge, Card } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { CONDITIONS } from './texts';

	// The conditions of the SD card standby (ADR-0471), each with its result and what the server
	// found, in its English words: those not met are the reason it is not offered.
	type Props = {
		conditions: StandbyCondition[];
	};

	let { conditions }: Props = $props();
</script>

<Card title={$t('standby.conditions.title')} padding="none">
	<ul class="divide-y divide-border" data-testid="standby-conditions">
		{#each conditions as condition (condition.id)}
			<li
				class="flex flex-wrap items-start justify-between gap-3 px-5 py-3"
				data-testid="standby-condition-{condition.id}"
			>
				<div class="min-w-0 flex-1">
					<p class="font-medium">{$t(CONDITIONS[condition.id])}</p>
					<p class="text-sm text-content-secondary break-words">{condition.detail}</p>
				</div>
				<Badge variant={condition.met ? 'success' : 'error'} dot>
					{$t(condition.met ? 'standby.met' : 'standby.unmet')}
				</Badge>
			</li>
		{/each}
	</ul>
</Card>
