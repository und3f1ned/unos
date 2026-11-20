<script lang="ts">
	import { Plus, RefreshCw } from 'lucide-svelte';
	import type { FirewallRule, FirewallStatus } from '$lib/api/security';
	import { Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import FirewallRuleItem from './FirewallRuleItem.svelte';
	import { labelOf, POLICIES } from './firewall';

	// What Configure opens on a host with ufw (ADR-0416): the firewall's state and default
	// policies with Enable or Disable and Reset, and its rules with Add rule.

	type Props = {
		status: FirewallStatus;
		/** A change is being sent: the buttons wait. */
		busy: boolean;
		/** Enable was pressed: the card asks first. */
		onenable: () => void;
		ondisable: () => void;
		onreset: () => void;
		onadd: () => void;
		ondeleterule: (rule: FirewallRule) => void;
	};

	let { status, busy, onenable, ondisable, onreset, onadd, ondeleterule }: Props = $props();
</script>

<div class="mt-4 space-y-4">
	<div class="flex flex-wrap items-center justify-between gap-2 p-3 bg-surface-overlay rounded-lg">
		<div class="min-w-0">
			<p class="font-medium">{$t('firewall.status')}</p>
			<p class="text-sm text-content-secondary">
				{$t('firewall.defaults', {
					incoming: labelOf(POLICIES, status.default_incoming, $t),
					outgoing: labelOf(POLICIES, status.default_outgoing, $t)
				})}
			</p>
		</div>
		<div class="flex flex-wrap gap-2">
			{#if status.enabled}
				<Button variant="danger" disabled={busy} onclick={ondisable}>{$t('firewall.disable')}</Button>
			{:else}
				<Button variant="success" disabled={busy} onclick={onenable}>{$t('firewall.enable')}</Button>
			{/if}
			<Button variant="secondary" disabled={busy} onclick={onreset}>
				<RefreshCw class="w-4 h-4" />
				{$t('firewall.reset')}
			</Button>
		</div>
	</div>

	<p class="text-sm text-content-secondary">{$t('firewall.unosHint')} {$t('firewall.docker')}</p>

	<div class="flex flex-wrap justify-between items-center gap-2">
		<h4 class="font-medium">{$t('firewall.rules')}</h4>
		<Button variant="primary" size="sm" disabled={busy} onclick={onadd}>
			<Plus class="w-4 h-4" />
			{$t('firewall.addRule')}
		</Button>
	</div>

	{#if status.rules.length === 0}
		<p class="text-sm text-content-secondary text-center py-4">{$t('firewall.noRules')}</p>
	{:else}
		<ul class="space-y-2" aria-label={$t('firewall.rules')}>
			{#each status.rules as rule (rule.id)}
				<FirewallRuleItem {rule} {busy} ondelete={() => ondeleterule(rule)} />
			{/each}
		</ul>
	{/if}
</div>
