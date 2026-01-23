<script lang="ts">
  import { onMount } from "svelte";
  import { getLogs, getConfigs, type NotificationLog, type NotificationConfig } from "$lib/api/notifications";
  import { Bell, AlertTriangle, Info, CheckCircle, RefreshCw, Clock, Settings } from "lucide-svelte";

  let logs: NotificationLog[] = [];
  let configs: NotificationConfig[] = [];
  let loading = true;
  let activeView: "logs" | "settings" = "logs";

  onMount(() => loadData());

  async function loadData() {
    loading = true;
    try {
      const [logsData, configsData] = await Promise.all([
        getLogs().catch(() => []),
        getConfigs().catch(() => [])
      ]);
      logs = Array.isArray(logsData) ? logsData : [];
      configs = Array.isArray(configsData) ? configsData : [];
    } catch (e) {
      console.error(e);
    }
    loading = false;
  }

  function getIcon(type: string) {
    if (type === "error" || type === "warning") return AlertTriangle;
    if (type === "success") return CheckCircle;
    return Info;
  }

  function getColor(type: string): string {
    if (type === "error") return "text-red-400";
    if (type === "warning") return "text-yellow-400";
    if (type === "success") return "text-green-400";
    return "text-blue-400";
  }
</script>

<div class="space-y-4">
  <div class="flex items-center justify-between">
    <h2 class="text-xl font-bold">Notifications</h2>
    <div class="flex items-center gap-2">
      <div class="flex bg-slate-700 rounded-lg p-1">
        <button
          on:click={() => activeView = "logs"}
          class="px-3 py-1 text-sm rounded {activeView === 'logs' ? 'bg-blue-600' : ''}"
        >
          History
        </button>
        <button
          on:click={() => activeView = "settings"}
          class="px-3 py-1 text-sm rounded {activeView === 'settings' ? 'bg-blue-600' : ''}"
        >
          Settings
        </button>
      </div>
      <button on:click={loadData} class="p-2 bg-slate-700 rounded-lg">
        <RefreshCw class="w-4 h-4 {loading ? 'animate-spin' : ''}" />
      </button>
    </div>
  </div>

  {#if activeView === "logs"}
    <div class="bg-slate-800 rounded-lg p-4 max-h-96 overflow-y-auto">
      {#if loading}
        <div class="flex justify-center py-8">
          <RefreshCw class="w-6 h-6 animate-spin text-blue-500" />
        </div>
      {:else if logs.length === 0}
        <div class="text-center py-8 text-slate-400">
          <Bell class="w-10 h-10 mx-auto mb-2 opacity-50" />
          <p>No notification history</p>
        </div>
      {:else}
        <div class="space-y-2">
          {#each logs as log}
            <div class="flex items-start gap-3 p-3 rounded-lg {log.success ? 'bg-slate-700/30' : 'bg-red-900/20'}">
              <svelte:component this={getIcon(log.event?.type || 'info')} class="w-5 h-5 mt-0.5 {getColor(log.event?.type || 'info')}" />
              <div class="flex-1 min-w-0">
                <p class="font-medium text-sm">{log.event?.title || 'Notification'}</p>
                <p class="text-xs text-slate-400">{log.event?.message || ''}</p>
                <p class="text-xs text-slate-500 mt-1">via {log.channel}</p>
              </div>
              <div class="text-right">
                <span class="text-xs {log.success ? 'text-green-400' : 'text-red-400'}">
                  {log.success ? 'Sent' : 'Failed'}
                </span>
                <p class="text-xs text-slate-500 flex items-center gap-1 mt-1">
                  <Clock class="w-3 h-3" />
                  {new Date(log.sent_at).toLocaleString()}
                </p>
              </div>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  {:else}
    <div class="bg-slate-800 rounded-lg p-4">
      {#if loading}
        <div class="flex justify-center py-8">
          <RefreshCw class="w-6 h-6 animate-spin text-blue-500" />
        </div>
      {:else if configs.length === 0}
        <div class="text-center py-8 text-slate-400">
          <Settings class="w-10 h-10 mx-auto mb-2 opacity-50" />
          <p>No notification channels configured</p>
          <p class="text-sm mt-2">Configure email or Telegram notifications in Settings</p>
        </div>
      {:else}
        <div class="space-y-3">
          {#each configs as config}
            <div class="flex items-center justify-between p-3 bg-slate-700 rounded-lg">
              <div class="flex items-center gap-3">
                <span class="px-2 py-1 text-xs rounded bg-slate-600 uppercase">{config.channel}</span>
                <span class="font-medium">{config.name}</span>
              </div>
              <span class="px-2 py-1 text-xs rounded {config.enabled ? 'bg-green-500/20 text-green-400' : 'bg-slate-600 text-slate-400'}">
                {config.enabled ? 'Enabled' : 'Disabled'}
              </span>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  {/if}
</div>
