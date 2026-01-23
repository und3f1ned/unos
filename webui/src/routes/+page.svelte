<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { Server, RefreshCw, Clock } from "lucide-svelte";
  import SystemStatsWidget from "$lib/components/widgets/SystemStatsWidget.svelte";
  import StorageWidget from "$lib/components/widgets/StorageWidget.svelte";
  import ContainersWidget from "$lib/components/widgets/ContainersWidget.svelte";
  import QuickActionsWidget from "$lib/components/widgets/QuickActionsWidget.svelte";
  import RecentActivityWidget from "$lib/components/widgets/RecentActivityWidget.svelte";

  import { getSystemInfo, getSystemStats, type SystemInfo, type SystemStats } from "$lib/api/system";
  import { getPools, type Pool } from "$lib/api/storage";
  import { getContainers, type Container } from "$lib/api/containers";
  import { getLogs, type NotificationLog } from "$lib/api/notifications";

  let systemInfo: SystemInfo | null = null;
  let stats: SystemStats | null = null;
  let pools: Pool[] = [];
  let containers: Container[] = [];
  let notificationLogs: NotificationLog[] = [];
  let loading = true;
  let lastUpdate: Date | null = null;

  // Network rate tracking
  let prevNetworkSent: number | null = null;
  let prevNetworkRecv: number | null = null;
  let prevNetworkTime: number | null = null;
  let networkUpRate: number = 0;
  let networkDownRate: number = 0;

  let autoRefresh = true;
  let refreshInterval: ReturnType<typeof setInterval> | null = null;

  onMount(async () => {
    await loadData();
    if (autoRefresh) {
      startAutoRefresh();
    }
  });

  onDestroy(() => {
    stopAutoRefresh();
  });

  function startAutoRefresh() {
    refreshInterval = setInterval(loadStats, 3000);
  }

  function stopAutoRefresh() {
    if (refreshInterval) {
      clearInterval(refreshInterval);
      refreshInterval = null;
    }
  }

  function calculateNetworkRates(newStats: SystemStats) {
    const now = Date.now();
    
    if (prevNetworkSent !== null && prevNetworkRecv !== null && prevNetworkTime !== null) {
      const timeDelta = (now - prevNetworkTime) / 1000; // seconds
      if (timeDelta > 0) {
        const sentDelta = newStats.network_sent - prevNetworkSent;
        const recvDelta = newStats.network_recv - prevNetworkRecv;
        
        // Handle counter reset (e.g., after reboot)
        if (sentDelta >= 0 && recvDelta >= 0) {
          networkUpRate = sentDelta / timeDelta;
          networkDownRate = recvDelta / timeDelta;
        }
      }
    }
    
    prevNetworkSent = newStats.network_sent;
    prevNetworkRecv = newStats.network_recv;
    prevNetworkTime = now;
  }

  async function loadData() {
    loading = true;
    try {
      const [infoData, statsData, poolsData, containersData, logsData] = await Promise.all([
        getSystemInfo(),
        getSystemStats(),
        getPools().catch(() => []),
        getContainers().catch(() => []),
        getLogs().catch(() => [])
      ]);

      systemInfo = infoData;
      stats = statsData;
      calculateNetworkRates(statsData);
      pools = Array.isArray(poolsData) ? poolsData : [];
      containers = Array.isArray(containersData) ? containersData : [];
      notificationLogs = Array.isArray(logsData) ? logsData : [];
      lastUpdate = new Date();
    } catch (e) {
      console.error("Failed to load dashboard data:", e);
    } finally {
      loading = false;
    }
  }

  async function loadStats() {
    try {
      stats = await getSystemStats();
      calculateNetworkRates(stats);
      lastUpdate = new Date();
    } catch (e) {
      console.error("Failed to load stats:", e);
    }
  }

  function formatUptime(bootTime: number): string {
    // bootTime is Unix timestamp of boot
    const now = Date.now() / 1000;
    const seconds = Math.floor(now - bootTime);
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    if (days > 0) return days + "d " + hours + "h";
    if (hours > 0) return hours + "h " + mins + "m";
    return mins + "m";
  }

  function formatTime(date: Date): string {
    return date.toLocaleTimeString();
  }

  // Get first temperature value from temperatures object
  function getFirstTemperature(temps: Record<string, number> | undefined): number | null {
    if (!temps) return null;
    const values = Object.values(temps);
    return values.length > 0 ? Math.round(values[0]) : null;
  }

  // Transform pools for widget
  $: poolsForWidget = pools.map(p => ({
    name: p.name,
    size: p.total_size || 0,
    used: p.used_size || 0,
    free: (p.total_size || 0) - (p.used_size || 0),
    health: "healthy"
  }));

  // Transform containers for widget
  $: containersForWidget = containers.map(c => ({
    id: c.id,
    name: c.name,
    state: c.state,
    image: c.image
  }));

  // Transform notification logs for widget
  $: notificationsForWidget = notificationLogs.map(log => ({
    id: log.id,
    type: log.event?.type || "info",
    title: log.event?.title || "Notification",
    message: log.event?.message || "",
    created_at: log.sent_at,
    read: log.success
  }));
</script>

<div class="space-y-6">
  <!-- Header -->
  <div class="flex items-center justify-between">
    <div class="flex items-center gap-3">
      <Server class="w-8 h-8 text-blue-500" />
      <div>
        <h1 class="text-2xl font-bold">{systemInfo?.hostname || "UNOS"}</h1>
        {#if systemInfo}
          <p class="text-sm text-slate-400">
            {systemInfo.os} • Uptime: {formatUptime(systemInfo.uptime)}
          </p>
        {/if}
      </div>
    </div>
    <div class="flex items-center gap-4">
      {#if lastUpdate}
        <span class="text-xs text-slate-500 flex items-center gap-1">
          <Clock class="w-3 h-3" />
          Updated {formatTime(lastUpdate)}
        </span>
      {/if}
      <button
        on:click={loadData}
        class="p-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
        disabled={loading}
      >
        <RefreshCw class="w-4 h-4 {loading ? 'animate-spin' : ''}" />
      </button>
    </div>
  </div>

  {#if loading && !stats}
    <div class="flex items-center justify-center py-12">
      <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
    </div>
  {:else}
    <!-- System Stats -->
    {#if stats}
      <SystemStatsWidget
        cpuPercent={stats.cpu_percent || 0}
        memoryPercent={stats.memory_percent || 0}
        memoryUsed={stats.memory_used || 0}
        memoryTotal={stats.memory_total || 0}
        networkUp={networkUpRate}
        networkDown={networkDownRate}
        temperature={getFirstTemperature(stats.temperatures)}
      />
    {/if}

    <!-- Main Grid -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <!-- Storage -->
      <StorageWidget pools={poolsForWidget} />

      <!-- Containers -->
      <ContainersWidget containers={containersForWidget} limit={5} />
    </div>

    <!-- Bottom Row -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <!-- Quick Actions -->
      <QuickActionsWidget />

      <!-- Recent Activity -->
      <RecentActivityWidget notifications={notificationsForWidget} limit={5} />
    </div>
  {/if}
</div>
