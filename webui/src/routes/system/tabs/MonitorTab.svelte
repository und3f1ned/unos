<script lang="ts">
  import { Cpu, MemoryStick, Network, HardDrive, Activity, Pause, Play, Wifi, WifiOff } from "lucide-svelte";
  import {
    stats,
    wsConnected,
    formatBytes,
    formatBytesPerSecond,
    type WebSocketStats,
    type CpuStats,
    type MemoryStats,
    type NetworkStats,
    type DiskIOStats,
    type ProcessInfo
  } from "$lib/stores/stats";
  import { t } from "$lib/i18n";

  // Local history for charts (built from WebSocket updates)
  let cpuHistory: number[] = [];
  let memoryHistory: number[] = [];
  const MAX_HISTORY = 30;

  // Subscribe to WebSocket stats
  let currentStats: WebSocketStats | null = null;
  $: {
    currentStats = $stats;
    if ($stats?.cpu) {
      cpuHistory = [...cpuHistory, $stats.cpu.percent].slice(-MAX_HISTORY);
      memoryHistory = [...memoryHistory, $stats.memory.percent].slice(-MAX_HISTORY);
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
      // i18n-ignore: the commands of an SVG path
      return i === 0 ? "M " + x + " " + y : "L " + x + " " + y;
    }).join(" ");
  }
</script>

<div class="space-y-6">
  <div class="flex items-center justify-between">
    <h2 class="text-xl font-bold">{$t("monitor.title")}</h2>
    <div class="flex items-center gap-2">
      <span class="flex items-center gap-1 px-3 py-1.5 text-sm rounded-lg transition-colors {$wsConnected ? 'bg-green-600' : 'bg-red-600'}">
        {#if $wsConnected}
          <Wifi class="w-4 h-4" />
          {$t("monitor.live")}
        {:else}
          <WifiOff class="w-4 h-4" />
          {$t("monitor.disconnected")}
        {/if}
      </span>
    </div>
  </div>

  {#if !currentStats?.cpu}
    <div class="flex items-center justify-center py-12">
      <div class="text-slate-400">{$t("monitor.waiting")}</div>
    </div>
  {:else}
    <!-- Stats Grid -->
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <!-- CPU -->
      <div class="bg-slate-800 rounded-lg p-4">
        <div class="flex items-center gap-2 mb-2">
          <Cpu class="w-5 h-5 text-blue-400" />
          <span class="text-slate-400 text-sm">{$t("monitor.cpu")}</span>
        </div>
        <p class="text-2xl font-bold {getPercentColor(currentStats.cpu.percent)}">{currentStats.cpu.percent.toFixed(1)}%</p>
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
          <span class="text-slate-400 text-sm">{$t("monitor.memory")}</span>
        </div>
        <p class="text-2xl font-bold {getPercentColor(currentStats.memory.percent)}">{currentStats.memory.percent.toFixed(1)}%</p>
        <p class="text-xs text-slate-500">{formatBytes(currentStats.memory.used)} / {formatBytes(currentStats.memory.total)}</p>
      </div>

      <!-- Network -->
      <div class="bg-slate-800 rounded-lg p-4">
        <div class="flex items-center gap-2 mb-2">
          <Network class="w-5 h-5 text-green-400" />
          <span class="text-slate-400 text-sm">{$t("monitor.network")}</span>
        </div>
        <p class="text-sm">↓ <span class="text-green-400 font-semibold">{formatBytesPerSecond(currentStats.network.bytes_recv_rate)}</span></p>
        <p class="text-sm">↑ <span class="text-orange-400 font-semibold">{formatBytesPerSecond(currentStats.network.bytes_sent_rate)}</span></p>
      </div>

      <!-- Disk I/O -->
      <div class="bg-slate-800 rounded-lg p-4">
        <div class="flex items-center gap-2 mb-2">
          <HardDrive class="w-5 h-5 text-yellow-400" />
          <span class="text-slate-400 text-sm">{$t("monitor.diskIo")}</span>
        </div>
        <p class="text-sm">{$t("monitor.read")} <span class="text-green-400 font-semibold">{formatBytesPerSecond(currentStats.disk_io.read_rate)}</span></p>
        <p class="text-sm">{$t("monitor.write")} <span class="text-red-400 font-semibold">{formatBytesPerSecond(currentStats.disk_io.write_rate)}</span></p>
      </div>
    </div>

    <!-- CPU Cores -->
    {#if currentStats.cpu.per_core.length > 1}
      <div class="bg-slate-800 rounded-lg p-4">
        <h3 class="text-sm font-semibold mb-3">{$t("monitor.cores")}</h3>
        <div class="grid grid-cols-8 gap-2">
          {#each currentStats.cpu.per_core as core, i}
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
        {$t("monitor.processes")}
      </h3>
      <table class="w-full text-sm">
        <thead>
          <tr class="text-slate-400 text-xs">
            <th class="text-left py-1">{$t("monitor.process.name")}</th>
            <th class="text-right py-1">{$t("monitor.process.cpu")}</th>
            <th class="text-right py-1">{$t("monitor.process.memory")}</th>
            <th class="text-left py-1">{$t("monitor.process.user")}</th>
          </tr>
        </thead>
        <tbody>
          {#each currentStats.processes.slice(0, 8) as proc}
            <tr class="border-t border-slate-700/50">
              <td class="py-1.5 font-mono">{proc.name}</td>
              <td class="py-1.5 text-right {getPercentColor(proc.cpu_percent)}">{proc.cpu_percent.toFixed(1)}%</td>
              <td class="py-1.5 text-right">{$t("monitor.megabytes", { value: proc.memory_mb.toFixed(0) })}</td>
              <td class="py-1.5 text-slate-500">{proc.username}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>
