<script lang="ts">
  import { Container, Play, Square, AlertCircle } from "lucide-svelte";

  export let containers: Array<{
    id: string;
    name: string;
    state: string;
    image: string;
  }> = [];
  export let compact: boolean = false;
  export let limit: number = 5;

  $: running = containers.filter(c => c.state === "running").length;
  $: stopped = containers.filter(c => c.state !== "running").length;
  $: displayContainers = containers.slice(0, limit);

  function getStateColor(state: string): string {
    switch (state) {
      case "running":
        return "text-green-400";
      case "exited":
      case "stopped":
        return "text-slate-400";
      default:
        return "text-yellow-400";
    }
  }

  function getStateIcon(state: string) {
    switch (state) {
      case "running":
        return Play;
      case "exited":
      case "stopped":
        return Square;
      default:
        return AlertCircle;
    }
  }
</script>

{#if compact}
  <div class="flex items-center gap-2 text-sm">
    <Container class="w-4 h-4 text-cyan-400" />
    <span class="text-green-400">{running} running</span>
    {#if stopped > 0}
      <span class="text-slate-500">•</span>
      <span class="text-slate-400">{stopped} stopped</span>
    {/if}
  </div>
{:else}
  <div class="bg-slate-800 rounded-lg p-4">
    <div class="flex items-center justify-between mb-4">
      <h3 class="font-semibold flex items-center gap-2">
        <Container class="w-5 h-5 text-cyan-400" />
        Containers
      </h3>
      <a href="/containers" class="text-sm text-blue-400 hover:text-blue-300">Manage →</a>
    </div>

    <!-- Summary -->
    <div class="flex items-center gap-4 mb-4 text-sm">
      <div class="flex items-center gap-1">
        <span class="w-2 h-2 rounded-full bg-green-500"></span>
        <span>{running} running</span>
      </div>
      <div class="flex items-center gap-1">
        <span class="w-2 h-2 rounded-full bg-slate-500"></span>
        <span>{stopped} stopped</span>
      </div>
    </div>

    {#if containers.length === 0}
      <p class="text-slate-400 text-sm">No containers found</p>
    {:else}
      <div class="space-y-2">
        {#each displayContainers as container}
          <div class="flex items-center justify-between p-2 bg-slate-700/50 rounded">
            <div class="flex items-center gap-2">
              <svelte:component this={getStateIcon(container.state)} class="w-4 h-4 {getStateColor(container.state)}" />
              <span class="font-medium text-sm">{container.name}</span>
            </div>
            <span class="text-xs text-slate-500 truncate max-w-32">{container.image.split(':')[0]}</span>
          </div>
        {/each}
        {#if containers.length > limit}
          <p class="text-xs text-slate-500 text-center">+{containers.length - limit} more containers</p>
        {/if}
      </div>
    {/if}
  </div>
{/if}
