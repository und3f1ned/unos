<script lang="ts">
	import { Bell, AlertTriangle, Info, CheckCircle } from 'lucide-svelte';
	import { Card, CardAction, EmptyState } from '$lib/components/ui';
	import { formatAgo } from '$lib/format';
	import { intlLocale, t } from '$lib/i18n';

	type Props = {
		notifications?: Array<{
			id: string;
			type: string;
			title: string;
			message: string;
			created_at: string;
			read: boolean;
		}>;
		limit?: number;
	};

	let { notifications = [], limit = 5 }: Props = $props();

	const displayNotifications = $derived(notifications.slice(0, limit));

	function getTypeIcon(type: string) {
		switch (type) {
			case 'error':
				return AlertTriangle;
			case 'warning':
				return AlertTriangle;
			case 'success':
				return CheckCircle;
			default:
				return Info;
		}
	}

	function getTypeColor(type: string): string {
		switch (type) {
			case 'error':
				return 'text-error';
			case 'warning':
				return 'text-warning';
			case 'success':
				return 'text-success';
			default:
				return 'text-info';
		}
	}

	// How long ago, in the language of the page; an older one as its date.
	function formatTime(iso: string): string {
		return formatAgo(iso, $intlLocale, $t);
	}
</script>

<!-- Unread rows have a bar in brand-ring on their left, in place of a fill (ADR-0428). -->
<Card class="h-full flex flex-col">
	<div class="card-header">
		<h3 class="card-title">
			<Bell class="w-5 h-5 text-content-secondary" aria-hidden="true" />
			{$t('dashboard.activity.title')}
		</h3>
		<CardAction href="/system#notifications">{$t('dashboard.activity.viewAll')}</CardAction>
	</div>

	{#if notifications.length === 0}
		<EmptyState compact icon={Bell} title={$t('dashboard.activity.none')} />
	{:else}
		<div class="flex flex-col">
			{#each displayNotifications as notification}
				{@const Icon = getTypeIcon(notification.type)}
				<div
					class="flex items-start gap-3 py-2 border-b border-border-muted last:border-b-0 {notification.read
						? ''
						: 'pl-2.5 shadow-[inset_2px_0_0_rgb(var(--color-brand-ring))]'}"
				>
					<Icon class="w-4 h-4 mt-0.5 shrink-0 {getTypeColor(notification.type)}" aria-hidden="true" />
					<div class="flex-1 min-w-0">
						<p class="text-sm font-medium truncate">{notification.title}</p>
						<p class="text-xs text-secondary truncate">{notification.message}</p>
					</div>
					<span class="text-xs text-muted whitespace-nowrap tabular">
						{formatTime(notification.created_at)}
					</span>
				</div>
			{/each}
		</div>
	{/if}
</Card>
