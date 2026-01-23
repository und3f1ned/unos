<script lang="ts">
  import { Cpu, MemoryStick, Network, Thermometer } from "lucide-svelte";

  export let cpuPercent: number = 0;
  export let memoryPercent: number = 0;
  export let memoryUsed: number = 0;
  export let memoryTotal: number = 0;
  export let networkUp: number = 0;
  export let networkDown: number = 0;
  export let temperature: number | null = null;
  export let compact: boolean = false;

  function formatBytes(bytes: number): string {
    if (bytes === 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
  }

  function formatRate(bytes: number): string {
    return formatBytes(bytes) + "/s";
  }

  function getPercentColor(percent: number): string {
    if (percent < 70) return "text-green-400";
    if (percent < 90) return "text-yellow-400";
    return "text-red-400";
  }

  function getPercentBg(percent: number): string {
    if (percent < 70) return "bg-green-500";
    if (percent < 90) return "bg-yellow-500";
    return "bg-red-500";
  }
</script>

{#if compact}
  <div class="flex items-center gap-4 text-sm">
    <div class="flex items-center gap-1">
      <Cpu class="w-4 h-4 text-blue-400" />
      <span class={getPercentColor(cpuPercent)}>{cpuPercent.toFixed(0)}%</span>
    </div>
    <div class="flex items-center gap-1">
      <MemoryStick class="w-4 h-4 text-purple-400" />
      <span class={getPercentColor(memoryPercent)}>{memoryPercent.toFixed(0)}%</span>
    </div>
    {#if temperature !== null}
      <div class="flex items-center gap-1">
        <Thermometer class="w-4 h-4 text-orange-400" />
        <span>{temperature}°C</span>
      </div>
    {/if}
  </div>
{:else}
  <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
    <!-- CPU -->
    <div class="bg-slate-800 rounded-lg p-4">
      <div class="flex items-center gap-2 mb-2">
        <Cpu class="w-5 h-5 text-blue-400" />
        <span class="text-slate-400 text-sm">CPU</span>
      </div>
      <p class="text-2xl font-bold {getPercentColor(cpuPercent)}">{cpuPercent.toFixed(1)}%</p>
      <div class="mt-2 h-1.5 bg-slate-700 rounded-full overflow-hidden">
        <div class="h-full {getPercentBg(cpuPercent)} transition-all" style="width: {cpuPercent}%"></div>
      </div>
    </div>

    <!-- Memory -->
    <div class="bg-slate-800 rounded-lg p-4">
      <div class="flex items-center gap-2 mb-2">
        <MemoryStick class="w-5 h-5 text-purple-400" />
        <span class="text-slate-400 text-sm">Memory</span>
      </div>
      <p class="text-2xl font-bold {getPercentColor(memoryPercent)}">{memoryPercent.toFixed(1)}%</p>
      <p class="text-xs text-slate-500">{formatBytes(memoryUsed)} / {formatBytes(memoryTotal)}</p>
      <div class="mt-2 h-1.5 bg-slate-700 rounded-full overflow-hidden">
        <div class="h-full {getPercentBg(memoryPercent)} transition-all" style="width: {memoryPercent}%"></div>
      </div>
    </div>

    <!-- Network -->
    <div class="bg-slate-800 rounded-lg p-4">
      <div class="flex items-center gap-2 mb-2">
        <Network class="w-5 h-5 text-green-400" />
        <span class="text-slate-400 text-sm">Network</span>
      </div>
      <div class="flex items-center gap-2">
        <span class="text-green-400">↓</span>
        <span class="text-lg font-semibold">{formatRate(networkDown)}</span>
      </div>
      <div class="flex items-center gap-2">
        <span class="text-orange-400">↑</span>
        <span class="text-lg font-semibold">{formatRate(networkUp)}</span>
      </div>
    </div>

    <!-- Temperature -->
    <div class="bg-slate-800 rounded-lg p-4">
      <div class="flex items-center gap-2 mb-2">
        <Thermometer class="w-5 h-5 text-orange-400" />
        <span class="text-slate-400 text-sm">Temperature</span>
      </div>
      {#if temperature !== null}
        <p class="text-2xl font-bold {temperature < 50 ? 'text-green-400' : temperature < 70 ? 'text-yellow-400' : 'text-red-400'}">{temperature}°C</p>
      {:else}
        <p class="text-2xl font-bold text-slate-500">N/A</p>
      {/if}
    </div>
  </div>
{/if}
