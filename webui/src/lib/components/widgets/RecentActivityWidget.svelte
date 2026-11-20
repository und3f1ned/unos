<script lang="ts">
	import { Bell, AlertTriangle, Info, CheckCircle } from 'lucide-svelte';
	import { Card } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { apiTime } from '$lib/time';

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

	// How long ago, in the language of the page.
	function formatTime(iso: string): string {
		const date = apiTime(iso);
		if (!date) return iso;
		const now = new Date();
		const diff = now.getTime() - date.getTime();
		const minutes = Math.floor(diff / 60000);
		const hours = Math.floor(diff / 3600000);
		const days = Math.floor(diff / 86400000);

		if (minutes < 1) return $t('dashboard.activity.justNow');
		if (minutes < 60) return $t('dashboard.activity.minutesAgo', { n: minutes });
		if (hours < 24) return $t('dashboard.activity.hoursAgo', { n: hours });
		return $t('dashboard.activity.daysAgo', { n: days });
	}
</script>

<!-- Unread rows have a bar in brand-ring on their left, in place of a fill (ADR-0428). -->
<Card class="h-full flex flex-col">
	<div class="card-header">
		<h3 class="card-title">
			<Bell class="w-5 h-5 text-content-secondary" aria-hidden="true" />
			{$t('dashboard.activity.title')}
		</h3>
		<a href="/system#notifications" class="link text-[13px]">{$t('dashboard.activity.viewAll')}</a>
	</div>

	{#if notifications.length === 0}
		<!-- Nothing yet: one quiet line, not an empty state as tall as the card (ADR-0432). -->
		<p class="text-sm text-secondary">{$t('dashboard.activity.none')}</p>
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
