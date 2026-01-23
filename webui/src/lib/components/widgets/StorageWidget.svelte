<script lang="ts">
  import { HardDrive, Database } from "lucide-svelte";

  export let pools: Array<{
    name: string;
    size: number;
    used: number;
    free: number;
    health: string;
  }> = [];
  export let compact: boolean = false;

  function formatBytes(bytes: number): string {
    if (bytes === 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
  }

  function getUsagePercent(used: number, size: number): number {
    return size > 0 ? (used / size) * 100 : 0;
  }

  function getHealthColor(health: string): string {
    switch (health?.toLowerCase()) {
      case "healthy":
      case "online":
        return "text-green-400";
      case "degraded":
        return "text-yellow-400";
      default:
        return "text-red-400";
    }
  }

  function getUsageColor(percent: number): string {
    if (percent < 70) return "bg-blue-500";
    if (percent < 90) return "bg-yellow-500";
    return "bg-red-500";
  }

  $: totalSize = pools.reduce((acc, p) => acc + p.size, 0);
  $: totalUsed = pools.reduce((acc, p) => acc + p.used, 0);
  $: totalPercent = totalSize > 0 ? (totalUsed / totalSize) * 100 : 0;
</script>

{#if compact}
  <div class="flex items-center gap-2 text-sm">
    <Database class="w-4 h-4 text-blue-400" />
    <span>{pools.length} pool{pools.length !== 1 ? 's' : ''}</span>
    <span class="text-slate-500">•</span>
    <span>{formatBytes(totalUsed)} / {formatBytes(totalSize)}</span>
  </div>
{:else}
  <div class="bg-slate-800 rounded-lg p-4">
    <div class="flex items-center justify-between mb-4">
      <h3 class="font-semibold flex items-center gap-2">
        <HardDrive class="w-5 h-5 text-blue-400" />
        Storage Pools
      </h3>
      <a href="/storage" class="text-sm text-blue-400 hover:text-blue-300">Manage →</a>
    </div>

    {#if pools.length === 0}
      <p class="text-slate-400 text-sm">No storage pools configured</p>
    {:else}
      <div class="space-y-3">
        {#each pools as pool}
          {@const percent = getUsagePercent(pool.used, pool.size)}
          <div>
            <div class="flex items-center justify-between mb-1">
              <div class="flex items-center gap-2">
                <span class="font-medium">{pool.name}</span>
                <span class="text-xs px-1.5 py-0.5 rounded {getHealthColor(pool.health)} bg-slate-700">
                  {pool.health}
                </span>
              </div>
              <span class="text-sm text-slate-400">{formatBytes(pool.used)} / {formatBytes(pool.size)}</span>
            </div>
            <div class="h-2 bg-slate-700 rounded-full overflow-hidden">
              <div class="h-full {getUsageColor(percent)} transition-all" style="width: {percent}%"></div>
            </div>
          </div>
        {/each}
      </div>

      <!-- Total -->
      <div class="mt-4 pt-3 border-t border-slate-700">
        <div class="flex items-center justify-between text-sm">
          <span class="text-slate-400">Total Storage</span>
          <span>{formatBytes(totalUsed)} / {formatBytes(totalSize)} ({totalPercent.toFixed(1)}%)</span>
        </div>
      </div>
    {/if}
  </div>
{/if}
