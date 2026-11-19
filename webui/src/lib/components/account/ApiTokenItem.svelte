<script lang="ts">
	import type { ApiToken } from '$lib/api/auth';
	import { Badge, Button } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';
	import { accessOf, areaNames, expired } from './apiTokens';

	// One API token of the list (ADR-0239): its name, prefix, access and areas, its dates, and
	// Disable or Enable, and Delete.

	type Props = {
		token: ApiToken;
		/** A token is being changed: the buttons wait. */
		busy: boolean;
		/** This token is being disabled or enabled. */
		toggling: boolean;
		ontoggle: () => void;
		ondelete: () => void;
	};

	let { token, busy, toggling, ontoggle, ondelete }: Props = $props();

	function formatted(value: string): string {
		return formatDateTime(value, $intlLocale);
	}
</script>

<li class="card-nested p-3 space-y-2 min-w-0" aria-label={token.name}>
	<div class="flex flex-wrap items-center gap-2">
		<span class="font-medium break-all">{token.name}</span>
		<span class="mono text-xs text-muted break-all">{token.token_prefix}</span>
		{#if !token.scope_known}
			<Badge variant="error">{$t('tokens.unknownScope')}</Badge>
		{:else}
			<Badge variant={token.read_only ? 'info' : token.areas === null ? 'warning' : 'neutral'}>
				{accessOf(token, $t)}
			</Badge>
		{/if}
		{#if !token.is_active}
			<Badge variant="neutral">{$t('tokens.disabled')}</Badge>
		{/if}
		{#if expired(token)}
			<Badge variant="warning">{$t('tokens.expired')}</Badge>
		{/if}
	</div>
	{#if token.scope_known}
		<p class="text-sm break-words">
			{token.areas === null
				? $t('tokens.areas.every')
				: $t('tokens.areas.some', { areas: areaNames(token.areas, $t) })}
		</p>
	{:else}
		<p class="text-sm text-error">{$t('tokens.unknownScopeHint')}</p>
	{/if}
	<p class="flex flex-wrap gap-x-3 text-xs text-muted">
		{#if token.created_at}
			<span>{$t('tokens.createdOn', { date: formatted(token.created_at) })}</span>
		{/if}
		<span>
			{token.expires_at ? $t('tokens.expiresOn', { date: formatted(token.expires_at) }) : $t('tokens.neverExpires')}
		</span>
		<span>
			{token.last_used_at ? $t('tokens.lastUsed', { date: formatted(token.last_used_at) }) : $t('tokens.neverUsed')}
		</span>
	</p>
	<div class="flex flex-wrap gap-2" role="group" aria-label={$t('tokens.actionsOf', { name: token.name })}>
		<Button variant="secondary" size="sm" loading={toggling} disabled={busy} onclick={ontoggle}>
			{token.is_active ? $t('tokens.disable') : $t('tokens.enable')}
		</Button>
		<Button variant="ghost" size="sm" disabled={busy} onclick={ondelete}>
			{$t('tokens.delete')}
		</Button>
	</div>
</li>
