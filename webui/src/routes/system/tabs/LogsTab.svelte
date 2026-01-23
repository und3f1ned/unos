<script lang="ts">
  import { onMount } from "svelte";
  import { getLogEntries, type LogEntry } from "$lib/api/logs";
  import { FileText, Search, RefreshCw } from "lucide-svelte";

  let entries: LogEntry[] = [];
  let loading = true;
  let search = "";
  let source = "dmesg";

  onMount(() => loadLogs());

  async function loadLogs() {
    loading = true;
    try {
      const result = await getLogEntries(source, 100, undefined, search || undefined);
      entries = result.entries;
    } catch (e) {
      console.error(e);
    }
    loading = false;
  }

  function getPriorityColor(p: string): string {
    if (p === "err" || p === "crit") return "text-red-400";
    if (p === "warning") return "text-yellow-400";
    return "text-slate-300";
  }
</script>

<div class="space-y-4">
  <div class="flex items-center justify-between">
    <h2 class="text-xl font-bold">System Logs</h2>
    <div class="flex items-center gap-2">
      <select bind:value={source} on:change={loadLogs} class="px-3 py-1.5 bg-slate-700 rounded-lg text-sm">
        <option value="dmesg">Kernel (dmesg)</option>
        <option value="journal">Journal</option>
      </select>
      <button on:click={loadLogs} class="p-2 bg-slate-700 rounded-lg">
        <RefreshCw class="w-4 h-4 {loading ? 'animate-spin' : ''}" />
      </button>
    </div>
  </div>

  <div class="flex items-center gap-2">
    <Search class="w-4 h-4 text-slate-400" />
    <input
      type="text"
      bind:value={search}
      on:keydown={(e) => e.key === 'Enter' && loadLogs()}
      placeholder="Search logs..."
      class="flex-1 px-3 py-2 bg-slate-700 rounded-lg text-sm"
    />
  </div>

  <div class="bg-slate-800 rounded-lg p-4 max-h-96 overflow-y-auto font-mono text-xs space-y-1">
    {#each entries as entry}
      <div class="flex gap-2 hover:bg-slate-700/50 px-2 py-1 rounded">
        <span class="text-slate-500 w-20 shrink-0">{entry.timestamp.slice(11, 19)}</span>
        <span class="w-16 shrink-0 {getPriorityColor(entry.priority)}">{entry.priority}</span>
        <span class="text-slate-400 truncate">{entry.message}</span>
      </div>
    {:else}
      <p class="text-slate-500 text-center py-4">No logs found</p>
    {/each}
  </div>
</div>
