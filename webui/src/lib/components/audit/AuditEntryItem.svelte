<script lang="ts">
	import type { AuditEntry, AuditSeverity } from '$lib/api/audit';
	import { Badge } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import { apiTime, formatTimeOfDay } from '$lib/time';
	import { agentOf, markerClass, resourceOf, textOf } from './timeline';

	// One entry of the timeline: its time in the browser's time zone, its action, its marks and
	// its details.

	type Props = {
		entry: AuditEntry;
		/** The keys of the actions' and the resource types' texts, by value (the page's maps). */
		actionNames: Record<string, string>;
		resourceTypes: Record<string, string>;
	};

	let { entry, actionNames, resourceTypes }: Props = $props();

	// The severities that an entry shows, by the keys of their texts in the catalog (ADR-0287).
	const SEVERITIES: Record<Exclude<AuditSeverity, 'info'>, { key: string; variant: 'warning' | 'error' }> = {
		warning: { key: 'audit.severity.warning', variant: 'warning' },
		critical: { key: 'audit.severity.critical', variant: 'error' }
	};

	// An entry the server files under "other" (an API token's, for one) is headed by what it says
	// it did, not by "Other".
	const headline = $derived(
		entry.action === 'other' && entry.details ? entry.details : textOf(actionNames, entry.action, $t)
	);
	const details = $derived(entry.action === 'other' ? '' : (entry.details ?? ''));
	// One mark for one fact: a failure is "Failed", not "Failed" and "Warning" too; a critical
	// entry also says Critical, failed or not.
	const severity = $derived(entry.severity !== 'info' ? SEVERITIES[entry.severity] : undefined);
	const agent = $derived(entry.user_agent ? agentOf(entry.user_agent) : null);
	const resource = $derived(resourceOf(entry, resourceTypes, $t));
</script>

<li class="relative pb-5 pl-6 last:pb-1">
	<span class="absolute -left-[5px] top-1.5 h-2.5 w-2.5 rounded-full {markerClass(entry)}" aria-hidden="true"></span>
	<div class="flex flex-wrap items-center gap-2">
		<time datetime={apiTime(entry.timestamp)?.toISOString() ?? entry.timestamp} class="mono text-content-muted"
			>{formatTimeOfDay(entry.timestamp, $intlLocale)}</time
		>
		<span class="min-w-0 break-words font-medium text-content">{headline}</span>
		{#if !entry.success}
			<Badge variant="error">{$t('audit.failed')}</Badge>
		{/if}
		{#if entry.severity === 'critical' || (entry.success && entry.severity !== 'info')}
			<Badge variant={severity?.variant ?? 'neutral'}>{severity ? $t(severity.key) : entry.severity}</Badge>
		{/if}
		{#if entry.chain === 'broken'}
			<Badge variant="error">{$t('audit.chain.broken')}</Badge>
		{/if}
	</div>
	{#if entry.chain === 'broken'}
		<p class="mt-1 text-sm text-status-error">{$t('audit.chain.brokenHint')}</p>
	{/if}
	<!-- A token's name of 64 characters, a name tried at sign-in of 128 and an
	     IPv6 address break within the entry on a phone (ADR-0302). -->
	{#if details}
		<p class="mt-1 break-words text-sm text-content">{details}</p>
	{/if}
	<dl class="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm">
		{#if entry.username}
			<div class="flex gap-1">
				<dt class="text-content-muted">{$t('audit.field.user')}</dt>
				<dd class="break-all text-content-secondary">{entry.username}</dd>
			</div>
		{/if}
		{#if entry.ip_address}
			<div class="flex gap-1">
				<dt class="text-content-muted">{$t('audit.field.address')}</dt>
				<dd class="break-all text-content-secondary">{entry.ip_address}</dd>
			</div>
		{/if}
		{#if resource}
			<div class="flex min-w-0 gap-1">
				<dt class="text-content-muted">{$t('audit.field.resource')}</dt>
				<dd class="break-all text-content-secondary">{resource}</dd>
			</div>
		{/if}
		{#if entry.user_agent}
			<!-- The browser in a few words; the whole user agent is its hover text. -->
			<div class="flex min-w-0 gap-1">
				<dt class="text-content-muted">{$t('audit.field.browser')}</dt>
				<dd class="break-all text-content-secondary" title={agent ? entry.user_agent : undefined}>
					{agent ? (agent.os ? $t('audit.agent', { browser: agent.browser, os: agent.os }) : agent.browser) : entry.user_agent}
				</dd>
			</div>
		{/if}
	</dl>
</li>
