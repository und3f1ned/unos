<script lang="ts">
	import type { Container } from '$lib/api/containers';
	import type { NotificationLog } from '$lib/api/notifications';
	import type { Pool } from '$lib/api/storage';
	import { t } from '$lib/i18n';
	import { Skeleton } from '$lib/components/ui';
	import type { WebSocketStats } from '$lib/stores/stats';
	import {
		ContainersWidget,
		RecentActivityWidget,
		StorageWidget,
		SystemStatsWidget
	} from '$lib/components/widgets';

	// One widget of the dashboard by its id (ADR-0395, $lib/dashboard-layout), with the data the
	// page has read: the API's answers, which each widget takes in its own form, and the live stats.

	type Props = {
		id: string;
		pools: Pool[];
		containers: Container[];
		notificationLogs: NotificationLog[];
		/** The last message of the live stats; until the first, a stats card is a skeleton of its tile (ADR-0430). */
		stats: WebSocketStats | null;
	};

	let { id, pools, containers, notificationLogs, stats }: Props = $props();

	// The CPU's temperature, which the server chooses among the host's sensors (ADR-0346), in whole
	// degrees; null on a host without sensors.
	function wholeDegrees(celsius: number | null | undefined): number | null {
		return celsius == null ? null : Math.round(celsius);
	}

	// Transform pools for widget
	const poolsForWidget = $derived(
		pools.map((p) => ({
			name: p.name,
			size: p.total_size || 0,
			used: p.used_size || 0,
			free: (p.total_size || 0) - (p.used_size || 0),
			health: p.health ?? 'unknown',
			mounted: p.mounted ?? !!p.mountpoint,
			missingDevices: p.missing_devices ?? 0,
			deviceErrors: p.device_errors ?? 0,
			readOnly: p.read_only ?? false,
			forcedReadOnly: p.forced_read_only ?? false
		}))
	);

	// Transform containers for widget
	const containersForWidget = $derived(
		containers.map((c) => ({
			id: c.id,
			name: c.name,
			state: c.state,
			image: c.image
		}))
	);

	// Transform notification logs for widget
	const notificationsForWidget = $derived(
		notificationLogs.map((log) => ({
			id: log.id,
			type: log.event?.type || 'info',
			title: log.event?.title || $t('dashboard.notification'),
			message: log.event?.message || '',
			created_at: log.sent_at,
			read: log.success
		}))
	);
</script>

{#if id === 'storage'}
	<StorageWidget pools={poolsForWidget} />
{:else if id === 'containers'}
	<ContainersWidget containers={containersForWidget} limit={5} />
{:else if id === 'recent-activity'}
	<RecentActivityWidget notifications={notificationsForWidget} limit={5} />
{:else if stats}
	<SystemStatsWidget
		only={id === 'memory' || id === 'network' || id === 'disk-io' || id === 'temperature' ? id : 'cpu'}
		cpuPercent={stats.cpu?.percent || 0}
		memoryPercent={stats.memory?.percent || 0}
		memoryUsed={stats.memory?.used || 0}
		memoryTotal={stats.memory?.total || 0}
		networkUp={stats.network?.bytes_sent_rate || 0}
		networkDown={stats.network?.bytes_recv_rate || 0}
		diskRead={stats.disk_io?.read_rate || 0}
		diskWrite={stats.disk_io?.write_rate || 0}
		temperature={wholeDegrees(stats.cpu?.temperature)}
		cpuCores={stats.cpu?.cores ?? 0}
	/>
{:else}
	<Skeleton shape="tile" />
{/if}
