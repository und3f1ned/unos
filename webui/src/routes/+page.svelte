<script lang="ts">
  import { onMount } from "svelte";
  import { Server, RefreshCw, Clock, UserCog } from "lucide-svelte";
  import { Card } from "$lib/components/ui";
  import SystemStatsWidget from "$lib/components/widgets/SystemStatsWidget.svelte";
  import StorageWidget from "$lib/components/widgets/StorageWidget.svelte";
  import ContainersWidget from "$lib/components/widgets/ContainersWidget.svelte";
  import QuickActionsWidget from "$lib/components/widgets/QuickActionsWidget.svelte";
  import RecentActivityWidget from "$lib/components/widgets/RecentActivityWidget.svelte";

  import { getSystemInfo, type SystemInfo } from "$lib/api/system";
  import { getPools, type Pool } from "$lib/api/storage";
  import { getContainers, type Container } from "$lib/api/containers";
  import { getLogs, type NotificationLog } from "$lib/api/notifications";
  import { stats, wsConnected, wsForbidden, type WebSocketStats } from "$lib/stores/stats";
  import { user } from "$lib/stores/auth";

  let systemInfo: SystemInfo | null = null;
  let pools: Pool[] = [];
  let containers: Container[] = [];
  let notificationLogs: NotificationLog[] = [];
  let loading = true;
  let lastUpdate: Date | null = null;

  // Subscribe to WebSocket stats
  let currentStats: WebSocketStats | null = null;
  $: currentStats = $stats;
  $: if ($stats) {
    lastUpdate = new Date();
  }

  onMount(async () => {
    await loadData();
  });

  // The layout renders this page only after the user is known. Pools,
  // containers and notifications are administrator-only APIs; regular
  // accounts get the system overview and the live stats only.
  $: isAdmin = $user?.is_admin === true;

  async function loadData() {
    loading = true;
    try {
      const [infoData, poolsData, containersData, logsData] = await Promise.all([
        getSystemInfo().catch((e) => {
          console.error("Failed to load system info:", e);
          return null;
        }),
        isAdmin ? getPools().catch(() => []) : [],
        isAdmin ? getContainers().catch(() => []) : [],
        isAdmin ? getLogs().catch(() => []) : []
      ]);

      systemInfo = infoData;
      pools = Array.isArray(poolsData) ? poolsData : [];
      containers = Array.isArray(containersData) ? containersData : [];
      notificationLogs = Array.isArray(logsData) ? logsData : [];
    } finally {
      loading = false;
    }
  }

  async function handleRefresh() {
    await loadData();
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
    health: p.health ?? "unknown",
    mounted: p.mounted ?? !!p.mountpoint,
    missingDevices: p.missing_devices ?? 0,
    deviceErrors: p.device_errors ?? 0,
    readOnly: p.read_only ?? false,
    forcedReadOnly: p.forced_read_only ?? false
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
      {#if $wsForbidden}
        <span class="text-xs text-warning">Live stats are not available for this account</span>
      {:else if !$wsConnected}
        <span class="text-xs text-warning">WebSocket disconnected</span>
      {:else if lastUpdate}
        <span class="text-xs text-slate-500 flex items-center gap-1">
          <Clock class="w-3 h-3" />
          Updated {formatTime(lastUpdate)}
        </span>
      {/if}
      <button
        on:click={handleRefresh}
        class="p-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
        disabled={loading}
      >
        <RefreshCw class="w-4 h-4 {loading ? 'animate-spin' : ''}" />
      </button>
    </div>
  </div>

  {#if loading && !currentStats}
    <div class="flex items-center justify-center py-12">
      <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
    </div>
  {:else}
    <!-- System Stats -->
    {#if currentStats}
      <SystemStatsWidget
        cpuPercent={currentStats.cpu_percent || 0}
        memoryPercent={currentStats.memory_percent || 0}
        memoryUsed={currentStats.memory_used || 0}
        memoryTotal={currentStats.memory_total || 0}
        networkUp={currentStats.network?.bytes_sent_rate || 0}
        networkDown={currentStats.network?.bytes_recv_rate || 0}
        temperature={getFirstTemperature(currentStats.temperatures)}
      />
    {/if}

    {#if isAdmin}
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
    {:else}
      <Card>
        <div class="flex items-start gap-3">
          <UserCog class="w-5 h-5 text-info mt-0.5" />
          <div>
            <p class="font-medium">Signed in as {$user?.username}</p>
            <p class="text-sm text-secondary">
              Storage, shares, containers and apps are managed by an administrator.
              You can change your password, two-factor authentication and review your
              sign-ins under <a href="/system#security" class="text-info hover:underline">Account</a>.
            </p>
          </div>
        </div>
      </Card>
    {/if}
  {/if}
</div>
