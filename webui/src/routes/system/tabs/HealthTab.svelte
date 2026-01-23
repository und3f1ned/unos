<script lang="ts">
  import { onMount } from "svelte";
  import { getDiskHealth, type SmartInfo } from "$lib/api/health";
  import { HardDrive, Thermometer, Check, X, AlertTriangle, RefreshCw } from "lucide-svelte";

  let disks: SmartInfo[] = [];
  let loading = true;

  onMount(() => loadHealth());

  async function loadHealth() {
    loading = true;
    try {
      const result = await getDiskHealth();
      disks = result.disks;
    } catch (e) {
      console.error(e);
    }
    loading = false;
  }

  function getHealthIcon(status: string) {
    if (status === "PASSED") return Check;
    if (status === "FAILED") return X;
    return AlertTriangle;
  }

  function getHealthColor(status: string): string {
    if (status === "PASSED") return "text-green-400";
    if (status === "FAILED") return "text-red-400";
    return "text-yellow-400";
  }
</script>

<div class="space-y-4">
  <div class="flex items-center justify-between">
    <h2 class="text-xl font-bold">Disk Health</h2>
    <button on:click={loadHealth} class="p-2 bg-slate-700 rounded-lg">
      <RefreshCw class="w-4 h-4 {loading ? 'animate-spin' : ''}" />
    </button>
  </div>

  {#if loading}
    <div class="flex justify-center py-8">
      <RefreshCw class="w-6 h-6 animate-spin text-blue-500" />
    </div>
  {:else}
    <div class="space-y-3">
      {#each disks as disk}
        <div class="bg-slate-800 rounded-lg p-4">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-3">
              <HardDrive class="w-8 h-8 text-blue-400" />
              <div>
                <p class="font-semibold">{disk.model}</p>
                <p class="text-sm text-slate-400">{disk.device} - {disk.capacity}</p>
              </div>
            </div>
            <div class="flex items-center gap-4">
              {#if disk.temperature !== null}
                <div class="flex items-center gap-1 text-sm">
                  <Thermometer class="w-4 h-4 text-orange-400" />
                  <span>{disk.temperature}C</span>
                </div>
              {/if}
              <div class="flex items-center gap-1 {getHealthColor(disk.health_status)}">
                <svelte:component this={getHealthIcon(disk.health_status)} class="w-5 h-5" />
                <span class="font-medium">{disk.health_status}</span>
              </div>
            </div>
          </div>
        </div>
      {:else}
        <p class="text-slate-400 text-center py-8">No disks found</p>
      {/each}
    </div>
  {/if}
</div>
