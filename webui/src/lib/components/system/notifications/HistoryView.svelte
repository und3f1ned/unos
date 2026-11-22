<script lang="ts">
	import { Bell, ChevronDown, ChevronUp } from 'lucide-svelte';
	import type { NotificationLog } from '$lib/api/notifications';
	import { Badge, Button, Card, EmptyState, Select, Skeleton, Segmented } from '$lib/components/ui';
	import { formatAgo } from '$lib/format';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';
	import { KIND_ORDER, TYPES, colorOf, iconOf, kindName, labelOf } from './channels';
	import type { HistoryFilter } from './history.svelte';

	// The History view: every notification sent, newest first, by channel and outcome; an entry
	// opens to its details.
	type Props = {
		logs: NotificationLog[];
		loading: boolean;
		filter: HistoryFilter;
	};

	let { logs, loading, filter }: Props = $props();

	let filteredLogs = $derived(logs.filter((log) => filter.matches(log)));
	let channelOptions = $derived([
		{ value: 'all', label: $t('notifications.history.allChannels') },
		...KIND_ORDER.map((kind) => ({ value: kind, label: kindName($t, kind) }))
	]);
</script>

<div class="space-y-4">
	<div class="flex flex-wrap items-end gap-3">
		<Select
			class="w-48"
			label={$t('notifications.history.channel')}
			bind:value={filter.channel}
			options={channelOptions}
		/>
		<!-- The outcome to filter by. -->
		<Segmented
			options={[
				{ value: 'all', label: $t('notifications.history.all') },
				{ value: 'success', label: $t('notifications.delivered') },
				{ value: 'failed', label: $t('notifications.failed') }
			]}
			value={filter.status}
			label={$t('notifications.history.status')}
			onchange={(status) => (filter.status = status)}
		/>
	</div>

	<Card>
		{#if loading && logs.length === 0}
			<div aria-busy="true"><Skeleton count={4} /></div>
		{:else if filteredLogs.length === 0}
			<EmptyState
				icon={Bell}
				title={$t('notifications.history.none')}
				description={$t(logs.length > 0 ? 'notifications.history.noMatch' : 'notifications.history.noneHint')}
			/>
		{:else}
			<ul class="divide-y divide-border" aria-label={$t('notifications.history.list')}>
				{#each filteredLogs as log (log.id)}
					{@const expanded = filter.expanded === log.id}
					{@const Icon = iconOf(log.event.type)}
					<li class="py-3">
						<Button
							variant="plain"
							type="button"
							class="w-full text-left"
							aria-expanded={expanded}
							onclick={() => (filter.expanded = expanded ? null : log.id)}
						>
							<span class="flex items-start gap-3">
								<Icon class="w-5 h-5 mt-0.5 flex-shrink-0 {colorOf(log.event.type)}" />
								<span class="flex-1 min-w-0">
									<span class="flex flex-wrap items-center gap-2">
										<span class="font-medium">{log.event.title}</span>
										<Badge variant="neutral">{kindName($t, log.channel)}</Badge>
										{#if log.channel_name}
											<span class="text-sm text-secondary">{log.channel_name}</span>
										{/if}
										{#if log.success}
											<Badge variant="success">{$t('notifications.delivered')}</Badge>
										{:else}
											<Badge variant="error">{$t('notifications.failed')}</Badge>
										{/if}
									</span>
									<span class="block text-sm text-muted truncate mt-1">{log.event.message}</span>
								</span>
								<span class="text-sm text-muted flex-shrink-0">{formatAgo(log.sent_at, $intlLocale, $t)}</span>
								{#if expanded}
									<ChevronUp class="w-4 h-4 mt-0.5 text-muted flex-shrink-0" />
								{:else}
									<ChevronDown class="w-4 h-4 mt-0.5 text-muted flex-shrink-0" />
								{/if}
							</span>
						</Button>
						{#if expanded}
							<dl class="mt-3 grid grid-cols-2 gap-3 text-sm">
								<div>
									<dt class="text-muted">{$t('notifications.history.sentAt')}</dt>
									<dd>{formatDateTime(log.sent_at, $intlLocale)}</dd>
								</div>
								<div>
									<dt class="text-muted">{$t('notifications.history.status')}</dt>
									<dd class={log.success ? 'text-success' : 'text-error'}>
										{$t(log.success ? 'notifications.delivered' : 'notifications.failed')}
									</dd>
								</div>
								<div>
									<dt class="text-muted">{$t('notifications.history.type')}</dt>
									<dd>{labelOf($t, TYPES, log.event.type)}</dd>
								</div>
								<div>
									<dt class="text-muted">{$t('notifications.history.channel')}</dt>
									<dd>{log.channel_name || kindName($t, log.channel)}</dd>
								</div>
								{#if log.error}
									<div class="col-span-2">
										<dt class="text-muted">{$t('notifications.history.error')}</dt>
										<dd class="mono-xs text-error break-words">{log.error}</dd>
									</div>
								{/if}
							</dl>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
	</Card>
</div>
