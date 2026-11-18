<script lang="ts">
	import { Bell, AlertTriangle, Info, CheckCircle, Clock } from 'lucide-svelte';
	import { Card } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	export let notifications: Array<{
		id: string;
		type: string;
		title: string;
		message: string;
		created_at: string;
		read: boolean;
	}> = [];
	export let limit: number = 5;

	$: displayNotifications = notifications.slice(0, limit);

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
	$: formatTime = (iso: string): string => {
		const date = new Date(iso);
		const now = new Date();
		const diff = now.getTime() - date.getTime();
		const minutes = Math.floor(diff / 60000);
		const hours = Math.floor(diff / 3600000);
		const days = Math.floor(diff / 86400000);

		if (minutes < 1) return $t('dashboard.activity.justNow');
		if (minutes < 60) return $t('dashboard.activity.minutesAgo', { n: minutes });
		if (hours < 24) return $t('dashboard.activity.hoursAgo', { n: hours });
		return $t('dashboard.activity.daysAgo', { n: days });
	};
</script>

<Card>
	<div class="card-header">
		<h3 class="card-title">
			<Bell class="w-5 h-5 text-warning" />
			{$t('dashboard.activity.title')}
		</h3>
		<a
			href="/system#notifications"
			class="text-sm text-info hover:text-status-info transition-colors">{$t('dashboard.activity.viewAll')}</a
		>
	</div>

	{#if notifications.length === 0}
		<p class="text-secondary text-sm text-center py-4">{$t('dashboard.activity.none')}</p>
	{:else}
		<div class="space-y-2">
			{#each displayNotifications as notification}
				<div
					class="flex items-start gap-3 p-2 rounded-lg {notification.read
						? ''
						: 'bg-surface-muted'}"
				>
					<svelte:component
						this={getTypeIcon(notification.type)}
						class="w-4 h-4 mt-0.5 {getTypeColor(notification.type)}"
					/>
					<div class="flex-1 min-w-0">
						<p class="text-sm font-medium truncate">{notification.title}</p>
						<p class="text-xs text-secondary truncate">{notification.message}</p>
					</div>
					<span class="text-xs text-muted whitespace-nowrap flex items-center gap-1">
						<Clock class="w-3 h-3" />
						{formatTime(notification.created_at)}
					</span>
				</div>
			{/each}
		</div>
	{/if}
</Card>
