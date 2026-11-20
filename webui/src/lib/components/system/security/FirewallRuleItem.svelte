<script lang="ts">
	import { Trash2 } from 'lucide-svelte';
	import type { FirewallRule } from '$lib/api/security';
	import { Badge, Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { labelOf, RULE_ACTIONS, ruleLine } from './firewall';

	// One rule of the host's firewall: its action, name, direction, protocol, port and addresses,
	// and Delete for a rule that is not UNOS's own.

	type Props = {
		rule: FirewallRule;
		/** A change is being sent: Delete waits. */
		busy: boolean;
		ondelete: () => void;
	};

	let { rule, busy, ondelete }: Props = $props();
</script>

<li>
	<div class="flex items-center justify-between gap-2 p-3 bg-surface-overlay rounded-lg">
		<div class="flex flex-wrap items-center gap-x-3 gap-y-1 min-w-0">
			<!-- The action in sentence case, readable alone ("Allow"), in its colour (ADR-0432). -->
			<Badge variant={rule.action === 'allow' ? 'success' : rule.action === 'limit' ? 'warning' : 'error'}>
				{labelOf(RULE_ACTIONS, rule.action, $t)}
			</Badge>
			<div class="min-w-0 break-words">
				<p class="font-medium">
					{rule.comment ?? rule.app ?? $t('firewall.ruleNumber', { n: rule.number })}
				</p>
				<p class="text-sm text-content-secondary">{ruleLine(rule, $t)}</p>
			</div>
			{#if rule.family === 'ipv6'}
				<Badge variant="info">{$t('firewall.ipv6')}</Badge>
			{/if}
			{#if rule.route}
				<Badge variant="neutral">{$t('firewall.route')}</Badge>
			{/if}
			{#if rule.unos}
				<Badge variant="purple">{$t('firewall.unos')}</Badge>
			{/if}
		</div>
		{#if !rule.unos}
			<Button
				variant="ghost-danger"
				size="icon"
				disabled={busy}
				aria-label={$t('firewall.deleteRule', { n: rule.number })}
				onclick={ondelete}
			>
				<Trash2 class="w-4 h-4" />
			</Button>
		{/if}
	</div>
</li>
