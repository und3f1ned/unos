<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { getAllStats, getTopProcesses, formatBytes, formatBytesPerSecond, type AllStats, type ProcessInfo } from "$lib/api/monitor";
  import { Cpu, MemoryStick, Network, HardDrive, Activity, RefreshCw, Pause, Play } from "lucide-svelte";

  let stats: AllStats | null = null;
  let processes: ProcessInfo[] = [];
  let loading = true;
  let autoRefresh = true;
  let refreshInterval: number | null = null;

  let cpuHistory: number[] = [];
  let memoryHistory: number[] = [];
  const MAX_HISTORY = 30;

  onMount(async () => {
    await loadData();
    if (autoRefresh) startAutoRefresh();
  });

  onDestroy(() => stopAutoRefresh());

  function startAutoRefresh() {
    refreshInterval = setInterval(loadData, 2000);
  }

  function stopAutoRefresh() {
    if (refreshInterval) {
      clearInterval(refreshInterval);
      refreshInterval = null;
    }
  }

  function toggleAutoRefresh() {
    autoRefresh = !autoRefresh;
    autoRefresh ? startAutoRefresh() : stopAutoRefresh();
  }

  async function loadData() {
    try {
      const [statsData, processData] = await Promise.all([
        getAllStats(),
        getTopProcesses("cpu", 8)
      ]);
      stats = statsData;
      processes = processData;
      cpuHistory = [...cpuHistory, stats.cpu.percent].slice(-MAX_HISTORY);
      memoryHistory = [...memoryHistory, stats.memory.percent].slice(-MAX_HISTORY);
      loading = false;
    } catch (e) {
      console.error(e);
      loading = false;
    }
  }

  function getPercentColor(p: number): string {
    return p < 70 ? "text-green-400" : p < 90 ? "text-yellow-400" : "text-red-400";
  }

  function getPercentBg(p: number): string {
    return p < 70 ? "bg-green-500" : p < 90 ? "bg-yellow-500" : "bg-red-500";
  }

  function miniChart(history: number[]): string {
    if (history.length < 2) return "";
    const max = 100;
    return history.map((v, i) => {
      const x = (i / (history.length - 1)) * 100;
      const y = 30 - (v / max) * 30;
      return i === 0 ? "M " + x + " " + y : "L " + x + " " + y;
    }).join(" ");
  }
</script>

<div class="space-y-6">
  <div class="flex items-center justify-between">
    <h2 class="text-xl font-bold">Resource Monitor</h2>
    <div class="flex items-center gap-2">
      <button
        on:click={toggleAutoRefresh}
        class="flex items-center gap-1 px-3 py-1.5 text-sm rounded-lg transition-colors {autoRefresh ? 'bg-green-600' : 'bg-slate-700'}"
      >
        {#if autoRefresh}<Pause class="w-4 h-4" />{:else}<Play class="w-4 h-4" />{/if}
        {autoRefresh ? "Live" : "Paused"}
      </button>
    </div>
  </div>

  {#if loading && !stats}
    <div class="flex items-center justify-center py-12">
      <RefreshCw class="w-6 h-6 animate-spin text-blue-500" />
    </div>
  {:else if stats}
    <!-- Stats Grid -->
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <!-- CPU -->
      <div class="bg-slate-800 rounded-lg p-4">
        <div class="flex items-center gap-2 mb-2">
          <Cpu class="w-5 h-5 text-blue-400" />
          <span class="text-slate-400 text-sm">CPU</span>
        </div>
        <p class="text-2xl font-bold {getPercentColor(stats.cpu.percent)}">{stats.cpu.percent.toFixed(1)}%</p>
        <div class="h-6 mt-2">
          <svg viewBox="0 0 100 30" class="w-full h-full">
            <path d={miniChart(cpuHistory)} fill="none" stroke="#3b82f6" stroke-width="1.5" />
          </svg>
        </div>
      </div>

      <!-- Memory -->
      <div class="bg-slate-800 rounded-lg p-4">
        <div class="flex items-center gap-2 mb-2">
          <MemoryStick class="w-5 h-5 text-purple-400" />
          <span class="text-slate-400 text-sm">Memory</span>
        </div>
        <p class="text-2xl font-bold {getPercentColor(stats.memory.percent)}">{stats.memory.percent.toFixed(1)}%</p>
        <p class="text-xs text-slate-500">{formatBytes(stats.memory.used)} / {formatBytes(stats.memory.total)}</p>
      </div>

      <!-- Network -->
      <div class="bg-slate-800 rounded-lg p-4">
        <div class="flex items-center gap-2 mb-2">
          <Network class="w-5 h-5 text-green-400" />
          <span class="text-slate-400 text-sm">Network</span>
        </div>
        <p class="text-sm">↓ <span class="text-green-400 font-semibold">{formatBytesPerSecond(stats.network.bytes_recv_rate)}</span></p>
        <p class="text-sm">↑ <span class="text-orange-400 font-semibold">{formatBytesPerSecond(stats.network.bytes_sent_rate)}</span></p>
      </div>

      <!-- Disk I/O -->
      <div class="bg-slate-800 rounded-lg p-4">
        <div class="flex items-center gap-2 mb-2">
          <HardDrive class="w-5 h-5 text-yellow-400" />
          <span class="text-slate-400 text-sm">Disk I/O</span>
        </div>
        <p class="text-sm">R: <span class="text-green-400 font-semibold">{formatBytesPerSecond(stats.disk_io.read_rate)}</span></p>
        <p class="text-sm">W: <span class="text-red-400 font-semibold">{formatBytesPerSecond(stats.disk_io.write_rate)}</span></p>
      </div>
    </div>

    <!-- CPU Cores -->
    {#if stats.cpu.per_core.length > 1}
      <div class="bg-slate-800 rounded-lg p-4">
        <h3 class="text-sm font-semibold mb-3">CPU Cores</h3>
        <div class="grid grid-cols-8 gap-2">
          {#each stats.cpu.per_core as core, i}
            <div class="text-center">
              <div class="h-12 bg-slate-700 rounded relative overflow-hidden">
                <div class="absolute bottom-0 left-0 right-0 {getPercentBg(core)}" style="height: {core}%"></div>
              </div>
              <p class="text-xs mt-1 text-slate-500">{i}</p>
            </div>
          {/each}
        </div>
      </div>
    {/if}

    <!-- Top Processes -->
    <div class="bg-slate-800 rounded-lg p-4">
      <h3 class="text-sm font-semibold mb-3 flex items-center gap-2">
        <Activity class="w-4 h-4" />
        Top Processes
      </h3>
      <table class="w-full text-sm">
        <thead>
          <tr class="text-slate-400 text-xs">
            <th class="text-left py-1">Name</th>
            <th class="text-right py-1">CPU</th>
            <th class="text-right py-1">Memory</th>
            <th class="text-left py-1">User</th>
          </tr>
        </thead>
        <tbody>
          {#each processes as proc}
            <tr class="border-t border-slate-700/50">
              <td class="py-1.5 font-mono">{proc.name}</td>
              <td class="py-1.5 text-right {getPercentColor(proc.cpu_percent)}">{proc.cpu_percent.toFixed(1)}%</td>
              <td class="py-1.5 text-right">{proc.memory_mb.toFixed(0)} MB</td>
              <td class="py-1.5 text-slate-500">{proc.username}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>
