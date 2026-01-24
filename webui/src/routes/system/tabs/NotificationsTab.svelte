<script lang="ts">
  import { onMount } from "svelte";
  import {
    getLogs, getConfigs, createConfig, updateConfig, deleteConfig,
    testNotification, getEventTypes,
    type NotificationLog, type NotificationConfig, type EventType
  } from "$lib/api/notifications";
  import {
    Bell, AlertTriangle, Info, CheckCircle, RefreshCw, Clock, Settings,
    Plus, Mail, Send, Trash2, Edit, ToggleLeft, ToggleRight, Zap,
    MessageCircle, ChevronDown, ChevronUp, Filter, X, Check
  } from "lucide-svelte";

  // State
  let logs: NotificationLog[] = [];
  let configs: NotificationConfig[] = [];
  let eventTypes: EventType[] = [];
  let loading = true;
  let activeView: "channels" | "history" = "channels";

  // Stats
  $: totalSent = logs.length;
  $: successCount = logs.filter(l => l.success).length;
  $: failedCount = logs.filter(l => !l.success).length;
  $: activeChannels = configs.filter(c => c.enabled).length;

  // Modal state
  let showModal = false;
  let modalMode: "create" | "edit" = "create";
  let editingConfig: NotificationConfig | null = null;
  let saving = false;
  let testing: string | null = null;
  let testResult: { success: boolean; message: string } | null = null;
  let deleteConfirm: string | null = null;

  // Form state
  let formChannel: "email" | "telegram" = "email";
  let formName = "";
  let formEnabled = true;
  let formEvents: string[] = [];

  // Email config
  let emailHost = "";
  let emailPort = 587;
  let emailUser = "";
  let emailPassword = "";
  let emailTls = true;
  let emailFrom = "";
  let emailTo = "";

  // Telegram config
  let telegramToken = "";
  let telegramChatId = "";

  // History filters
  let filterChannel: "all" | "email" | "telegram" = "all";
  let filterStatus: "all" | "success" | "failed" = "all";
  let expandedLog: string | null = null;

  onMount(() => loadData());

  async function loadData() {
    loading = true;
    try {
      const [logsData, configsData, eventsData] = await Promise.all([
        getLogs().catch(() => []),
        getConfigs().catch(() => []),
        getEventTypes().catch(() => [])
      ]);
      logs = Array.isArray(logsData) ? logsData : [];
      configs = Array.isArray(configsData) ? configsData : [];
      eventTypes = Array.isArray(eventsData) ? eventsData : [];
    } catch (e) {
      console.error(e);
    }
    loading = false;
  }

  function openCreateModal(channel: "email" | "telegram") {
    modalMode = "create";
    editingConfig = null;
    formChannel = channel;
    formName = channel === "email" ? "Email Notifications" : "Telegram Notifications";
    formEnabled = true;
    formEvents = [];

    // Reset email fields
    emailHost = "";
    emailPort = 587;
    emailUser = "";
    emailPassword = "";
    emailTls = true;
    emailFrom = "";
    emailTo = "";

    // Reset telegram fields
    telegramToken = "";
    telegramChatId = "";

    testResult = null;
    showModal = true;
  }

  function openEditModal(config: NotificationConfig) {
    modalMode = "edit";
    editingConfig = config;
    formChannel = config.channel;
    formName = config.name || "";
    formEnabled = config.enabled;
    formEvents = [...(config.events || [])];

    if (config.email_config) {
      emailHost = config.email_config.smtp_host;
      emailPort = config.email_config.smtp_port;
      emailUser = config.email_config.smtp_user;
      emailPassword = config.email_config.smtp_password;
      emailTls = config.email_config.smtp_tls;
      emailFrom = config.email_config.from_address;
      emailTo = config.email_config.to_addresses.join(", ");
    }

    if (config.telegram_config) {
      telegramToken = config.telegram_config.bot_token;
      telegramChatId = config.telegram_config.chat_id;
    }

    testResult = null;
    showModal = true;
  }

  function closeModal() {
    showModal = false;
    editingConfig = null;
    testResult = null;
  }

  async function handleSave() {
    saving = true;
    try {
      const config: NotificationConfig = {
        name: formName,
        channel: formChannel,
        enabled: formEnabled,
        events: formEvents,
        email_config: formChannel === "email" ? {
          smtp_host: emailHost,
          smtp_port: emailPort,
          smtp_user: emailUser,
          smtp_password: emailPassword,
          smtp_tls: emailTls,
          from_address: emailFrom,
          to_addresses: emailTo.split(",").map(e => e.trim()).filter(e => e)
        } : undefined,
        telegram_config: formChannel === "telegram" ? {
          bot_token: telegramToken,
          chat_id: telegramChatId
        } : undefined
      };

      if (modalMode === "edit" && editingConfig?.id) {
        await updateConfig(editingConfig.id, config);
      } else {
        await createConfig(config);
      }

      await loadData();
      closeModal();
    } catch (e: any) {
      console.error(e);
      testResult = { success: false, message: e.message || "Failed to save" };
    }
    saving = false;
  }

  async function handleTest(configId: string) {
    testing = configId;
    testResult = null;
    try {
      await testNotification(configId);
      testResult = { success: true, message: "Test notification sent successfully!" };
    } catch (e: any) {
      testResult = { success: false, message: e.message || "Test failed" };
    }
    testing = null;
  }

  async function handleDelete(configId: string) {
    try {
      await deleteConfig(configId);
      deleteConfirm = null;
      await loadData();
    } catch (e) {
      console.error(e);
    }
  }

  async function toggleEnabled(config: NotificationConfig) {
    try {
      await updateConfig(config.id!, { ...config, enabled: !config.enabled });
      await loadData();
    } catch (e) {
      console.error(e);
    }
  }

  function toggleEvent(eventId: string) {
    if (formEvents.includes(eventId)) {
      formEvents = formEvents.filter(e => e !== eventId);
    } else {
      formEvents = [...formEvents, eventId];
    }
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

  function getChannelIcon(channel: string) {
    return channel === "email" ? Mail : MessageCircle;
  }

  function formatDate(dateStr: string): string {
    const date = new Date(dateStr);
    const now = new Date();
    const diff = now.getTime() - date.getTime();

    if (diff < 60000) return "Just now";
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
    return date.toLocaleDateString();
  }

  $: filteredLogs = logs.filter(log => {
    if (filterChannel !== "all" && log.channel !== filterChannel) return false;
    if (filterStatus === "success" && !log.success) return false;
    if (filterStatus === "failed" && log.success) return false;
    return true;
  });
</script>

<div class="space-y-6">
  <!-- Stats Cards -->
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
    <div class="bg-slate-800 rounded-lg p-4">
      <div class="flex items-center gap-3">
        <div class="p-2 bg-blue-500/20 rounded-lg">
          <Bell class="w-5 h-5 text-blue-400" />
        </div>
        <div>
          <p class="text-2xl font-bold">{activeChannels}</p>
          <p class="text-xs text-slate-400">Active Channels</p>
        </div>
      </div>
    </div>
    <div class="bg-slate-800 rounded-lg p-4">
      <div class="flex items-center gap-3">
        <div class="p-2 bg-purple-500/20 rounded-lg">
          <Send class="w-5 h-5 text-purple-400" />
        </div>
        <div>
          <p class="text-2xl font-bold">{totalSent}</p>
          <p class="text-xs text-slate-400">Total Sent</p>
        </div>
      </div>
    </div>
    <div class="bg-slate-800 rounded-lg p-4">
      <div class="flex items-center gap-3">
        <div class="p-2 bg-green-500/20 rounded-lg">
          <CheckCircle class="w-5 h-5 text-green-400" />
        </div>
        <div>
          <p class="text-2xl font-bold">{successCount}</p>
          <p class="text-xs text-slate-400">Delivered</p>
        </div>
      </div>
    </div>
    <div class="bg-slate-800 rounded-lg p-4">
      <div class="flex items-center gap-3">
        <div class="p-2 bg-red-500/20 rounded-lg">
          <AlertTriangle class="w-5 h-5 text-red-400" />
        </div>
        <div>
          <p class="text-2xl font-bold">{failedCount}</p>
          <p class="text-xs text-slate-400">Failed</p>
        </div>
      </div>
    </div>
  </div>

  <!-- Header with tabs -->
  <div class="flex items-center justify-between">
    <div class="flex bg-slate-800 rounded-lg p-1">
      <button
        on:click={() => activeView = "channels"}
        class="px-4 py-2 text-sm rounded-lg transition-colors {activeView === 'channels' ? 'bg-blue-600' : 'hover:bg-slate-700'}"
      >
        <Settings class="w-4 h-4 inline mr-2" />
        Channels
      </button>
      <button
        on:click={() => activeView = "history"}
        class="px-4 py-2 text-sm rounded-lg transition-colors {activeView === 'history' ? 'bg-blue-600' : 'hover:bg-slate-700'}"
      >
        <Clock class="w-4 h-4 inline mr-2" />
        History
      </button>
    </div>
    <button on:click={loadData} class="p-2 bg-slate-800 hover:bg-slate-700 rounded-lg transition-colors">
      <RefreshCw class="w-4 h-4 {loading ? 'animate-spin' : ''}" />
    </button>
  </div>

  <!-- Channels View -->
  {#if activeView === "channels"}
    <div class="space-y-4">
      <!-- Add Channel Buttons -->
      <div class="flex gap-3">
        <button
          on:click={() => openCreateModal("email")}
          class="flex items-center gap-2 px-4 py-3 bg-slate-800 hover:bg-slate-700 rounded-lg border-2 border-dashed border-slate-600 hover:border-blue-500 transition-colors flex-1"
        >
          <Mail class="w-5 h-5 text-blue-400" />
          <div class="text-left">
            <p class="font-medium">Add Email</p>
            <p class="text-xs text-slate-400">SMTP notifications</p>
          </div>
        </button>
        <button
          on:click={() => openCreateModal("telegram")}
          class="flex items-center gap-2 px-4 py-3 bg-slate-800 hover:bg-slate-700 rounded-lg border-2 border-dashed border-slate-600 hover:border-blue-500 transition-colors flex-1"
        >
          <MessageCircle class="w-5 h-5 text-blue-400" />
          <div class="text-left">
            <p class="font-medium">Add Telegram</p>
            <p class="text-xs text-slate-400">Bot notifications</p>
          </div>
        </button>
      </div>

      <!-- Configured Channels -->
      {#if loading}
        <div class="flex justify-center py-12">
          <RefreshCw class="w-8 h-8 animate-spin text-blue-500" />
        </div>
      {:else if configs.length === 0}
        <div class="bg-slate-800 rounded-lg p-8 text-center">
          <Bell class="w-12 h-12 mx-auto mb-4 text-slate-600" />
          <h3 class="text-lg font-medium mb-2">No notification channels</h3>
          <p class="text-slate-400 text-sm">Add an email or Telegram channel to start receiving notifications</p>
        </div>
      {:else}
        <div class="space-y-3">
          {#each configs as config}
            <div class="bg-slate-800 rounded-lg p-4">
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-4">
                  <div class="p-3 rounded-lg {config.enabled ? 'bg-blue-500/20' : 'bg-slate-700'}">
                    <svelte:component
                      this={getChannelIcon(config.channel)}
                      class="w-6 h-6 {config.enabled ? 'text-blue-400' : 'text-slate-500'}"
                    />
                  </div>
                  <div>
                    <div class="flex items-center gap-2">
                      <h3 class="font-medium">{config.name || config.channel}</h3>
                      <span class="px-2 py-0.5 text-xs rounded bg-slate-700 uppercase">{config.channel}</span>
                    </div>
                    <p class="text-sm text-slate-400">
                      {#if config.email_config}
                        {config.email_config.to_addresses.join(", ")}
                      {:else if config.telegram_config}
                        Chat ID: {config.telegram_config.chat_id}
                      {/if}
                    </p>
                    {#if config.events && config.events.length > 0}
                      <div class="flex flex-wrap gap-1 mt-2">
                        {#each config.events.slice(0, 3) as event}
                          <span class="px-2 py-0.5 text-xs bg-slate-700 rounded">{event}</span>
                        {/each}
                        {#if config.events.length > 3}
                          <span class="px-2 py-0.5 text-xs bg-slate-700 rounded">+{config.events.length - 3} more</span>
                        {/if}
                      </div>
                    {/if}
                  </div>
                </div>
                <div class="flex items-center gap-2">
                  <button
                    on:click={() => toggleEnabled(config)}
                    class="p-2 rounded-lg hover:bg-slate-700 transition-colors"
                    title={config.enabled ? "Disable" : "Enable"}
                  >
                    {#if config.enabled}
                      <ToggleRight class="w-6 h-6 text-green-400" />
                    {:else}
                      <ToggleLeft class="w-6 h-6 text-slate-500" />
                    {/if}
                  </button>
                  <button
                    on:click={() => handleTest(config.id || "")}
                    disabled={testing === config.id || !config.enabled}
                    class="p-2 rounded-lg hover:bg-slate-700 transition-colors disabled:opacity-50"
                    title="Send test notification"
                  >
                    {#if testing === config.id}
                      <RefreshCw class="w-5 h-5 animate-spin text-blue-400" />
                    {:else}
                      <Zap class="w-5 h-5 text-yellow-400" />
                    {/if}
                  </button>
                  <button
                    on:click={() => openEditModal(config)}
                    class="p-2 rounded-lg hover:bg-slate-700 transition-colors"
                    title="Edit"
                  >
                    <Edit class="w-5 h-5 text-slate-400" />
                  </button>
                  {#if deleteConfirm === config.id}
                    <div class="flex items-center gap-1 bg-red-500/20 rounded-lg px-2">
                      <button
                        on:click={() => handleDelete(config.id || "")}
                        class="p-2 text-red-400 hover:text-red-300"
                        title="Confirm delete"
                      >
                        <Check class="w-5 h-5" />
                      </button>
                      <button
                        on:click={() => deleteConfirm = null}
                        class="p-2 text-slate-400 hover:text-slate-300"
                        title="Cancel"
                      >
                        <X class="w-5 h-5" />
                      </button>
                    </div>
                  {:else}
                    <button
                      on:click={() => deleteConfirm = config.id || null}
                      class="p-2 rounded-lg hover:bg-slate-700 transition-colors"
                      title="Delete"
                    >
                      <Trash2 class="w-5 h-5 text-red-400" />
                    </button>
                  {/if}
                </div>
              </div>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  {/if}

  <!-- History View -->
  {#if activeView === "history"}
    <div class="space-y-4">
      <!-- Filters -->
      <div class="flex gap-3 flex-wrap">
        <div class="flex items-center gap-2 bg-slate-800 rounded-lg p-1">
          <Filter class="w-4 h-4 text-slate-400 ml-2" />
          <select
            bind:value={filterChannel}
            class="bg-transparent text-sm py-1 pr-2 focus:outline-none"
          >
            <option value="all">All Channels</option>
            <option value="email">Email</option>
            <option value="telegram">Telegram</option>
          </select>
        </div>
        <div class="flex bg-slate-800 rounded-lg p-1">
          <button
            on:click={() => filterStatus = "all"}
            class="px-3 py-1 text-sm rounded {filterStatus === 'all' ? 'bg-slate-700' : ''}"
          >
            All
          </button>
          <button
            on:click={() => filterStatus = "success"}
            class="px-3 py-1 text-sm rounded {filterStatus === 'success' ? 'bg-green-600' : ''}"
          >
            Delivered
          </button>
          <button
            on:click={() => filterStatus = "failed"}
            class="px-3 py-1 text-sm rounded {filterStatus === 'failed' ? 'bg-red-600' : ''}"
          >
            Failed
          </button>
        </div>
      </div>

      <!-- Logs List -->
      <div class="bg-slate-800 rounded-lg divide-y divide-slate-700">
        {#if loading}
          <div class="flex justify-center py-12">
            <RefreshCw class="w-8 h-8 animate-spin text-blue-500" />
          </div>
        {:else if filteredLogs.length === 0}
          <div class="p-8 text-center">
            <Bell class="w-12 h-12 mx-auto mb-4 text-slate-600" />
            <h3 class="text-lg font-medium mb-2">No notifications yet</h3>
            <p class="text-slate-400 text-sm">
              {#if logs.length > 0}
                No notifications match your filters
              {:else}
                Notifications will appear here when sent
              {/if}
            </p>
          </div>
        {:else}
          {#each filteredLogs as log}
            <div class="p-4 hover:bg-slate-750 transition-colors">
              <button
                class="w-full text-left"
                on:click={() => expandedLog = expandedLog === log.id ? null : log.id}
              >
                <div class="flex items-start gap-3">
                  <div class="p-2 rounded-lg {log.success ? 'bg-slate-700' : 'bg-red-500/20'}">
                    <svelte:component
                      this={getIcon(log.event?.type || 'info')}
                      class="w-5 h-5 {getColor(log.event?.type || 'info')}"
                    />
                  </div>
                  <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2">
                      <p class="font-medium truncate">{log.event?.title || 'Notification'}</p>
                      <span class="px-2 py-0.5 text-xs rounded bg-slate-700 uppercase flex-shrink-0">
                        {log.channel}
                      </span>
                      {#if !log.success}
                        <span class="px-2 py-0.5 text-xs rounded bg-red-500/20 text-red-400 flex-shrink-0">
                          Failed
                        </span>
                      {/if}
                    </div>
                    <p class="text-sm text-slate-400 truncate mt-1">{log.event?.message || ''}</p>
                  </div>
                  <div class="text-right flex-shrink-0">
                    <p class="text-sm text-slate-400">{formatDate(log.sent_at)}</p>
                    {#if expandedLog === log.id}
                      <ChevronUp class="w-4 h-4 text-slate-500 ml-auto mt-1" />
                    {:else}
                      <ChevronDown class="w-4 h-4 text-slate-500 ml-auto mt-1" />
                    {/if}
                  </div>
                </div>
              </button>

              {#if expandedLog === log.id}
                <div class="mt-3 pt-3 border-t border-slate-700 text-sm">
                  <div class="grid grid-cols-2 gap-4">
                    <div>
                      <p class="text-slate-500">Sent at</p>
                      <p>{new Date(log.sent_at).toLocaleString()}</p>
                    </div>
                    <div>
                      <p class="text-slate-500">Status</p>
                      <p class="{log.success ? 'text-green-400' : 'text-red-400'}">
                        {log.success ? 'Delivered' : 'Failed'}
                      </p>
                    </div>
                    <div>
                      <p class="text-slate-500">Type</p>
                      <p class="capitalize">{log.event?.type || 'info'}</p>
                    </div>
                    <div>
                      <p class="text-slate-500">Channel</p>
                      <p class="capitalize">{log.channel}</p>
                    </div>
                  </div>
                  {#if log.error}
                    <div class="mt-3 p-3 bg-red-500/10 rounded-lg">
                      <p class="text-slate-500 mb-1">Error</p>
                      <p class="text-red-400 text-xs font-mono">{log.error}</p>
                    </div>
                  {/if}
                </div>
              {/if}
            </div>
          {/each}
        {/if}
      </div>
    </div>
  {/if}
</div>

<!-- Channel Configuration Modal -->
{#if showModal}
  <div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
    <div class="bg-slate-800 rounded-lg w-full max-w-lg max-h-[90vh] overflow-y-auto">
      <div class="sticky top-0 bg-slate-800 p-4 border-b border-slate-700">
        <div class="flex items-center justify-between">
          <h3 class="text-lg font-semibold flex items-center gap-2">
            <svelte:component this={getChannelIcon(formChannel)} class="w-5 h-5 text-blue-400" />
            {modalMode === "create" ? "Add" : "Edit"} {formChannel === "email" ? "Email" : "Telegram"} Channel
          </h3>
          <button on:click={closeModal} class="p-2 hover:bg-slate-700 rounded-lg">
            <X class="w-5 h-5" />
          </button>
        </div>
      </div>

      <div class="p-4 space-y-4">
        <!-- Name -->
        <div>
          <label class="block text-sm font-medium text-slate-400 mb-1">Channel Name</label>
          <input
            type="text"
            bind:value={formName}
            placeholder="e.g., Admin Alerts"
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
          />
        </div>

        <!-- Email Config -->
        {#if formChannel === "email"}
          <div class="space-y-4 pt-2">
            <div class="grid grid-cols-2 gap-4">
              <div class="col-span-2 sm:col-span-1">
                <label class="block text-sm font-medium text-slate-400 mb-1">SMTP Host</label>
                <input
                  type="text"
                  bind:value={emailHost}
                  placeholder="smtp.gmail.com"
                  class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
                />
              </div>
              <div class="col-span-2 sm:col-span-1">
                <label class="block text-sm font-medium text-slate-400 mb-1">Port</label>
                <input
                  type="number"
                  bind:value={emailPort}
                  placeholder="587"
                  class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>
            <div class="grid grid-cols-2 gap-4">
              <div>
                <label class="block text-sm font-medium text-slate-400 mb-1">Username</label>
                <input
                  type="text"
                  bind:value={emailUser}
                  placeholder="user@example.com"
                  class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label class="block text-sm font-medium text-slate-400 mb-1">Password</label>
                <input
                  type="password"
                  bind:value={emailPassword}
                  placeholder="••••••••"
                  class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>
            <div>
              <label class="block text-sm font-medium text-slate-400 mb-1">From Address</label>
              <input
                type="email"
                bind:value={emailFrom}
                placeholder="noreply@example.com"
                class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
              />
            </div>
            <div>
              <label class="block text-sm font-medium text-slate-400 mb-1">To Addresses (comma-separated)</label>
              <input
                type="text"
                bind:value={emailTo}
                placeholder="admin@example.com, user@example.com"
                class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
              />
            </div>
            <label class="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                bind:checked={emailTls}
                class="w-4 h-4 rounded border-slate-600 text-blue-600 focus:ring-blue-500"
              />
              <span class="text-sm">Use TLS/STARTTLS</span>
            </label>
          </div>
        {/if}

        <!-- Telegram Config -->
        {#if formChannel === "telegram"}
          <div class="space-y-4 pt-2">
            <div>
              <label class="block text-sm font-medium text-slate-400 mb-1">Bot Token</label>
              <input
                type="text"
                bind:value={telegramToken}
                placeholder="123456:ABC-DEF..."
                class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500 font-mono text-sm"
              />
              <p class="text-xs text-slate-500 mt-1">Get this from @BotFather on Telegram</p>
            </div>
            <div>
              <label class="block text-sm font-medium text-slate-400 mb-1">Chat ID</label>
              <input
                type="text"
                bind:value={telegramChatId}
                placeholder="-1001234567890"
                class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500 font-mono text-sm"
              />
              <p class="text-xs text-slate-500 mt-1">Your user ID or group chat ID</p>
            </div>
          </div>
        {/if}

        <!-- Event Types -->
        <div class="pt-2">
          <label class="block text-sm font-medium text-slate-400 mb-2">Notify on Events</label>
          <div class="grid grid-cols-1 gap-2 max-h-48 overflow-y-auto bg-slate-700/50 rounded-lg p-3">
            {#each eventTypes as event}
              <label class="flex items-start gap-3 cursor-pointer p-2 rounded hover:bg-slate-700">
                <input
                  type="checkbox"
                  checked={formEvents.includes(event.id)}
                  on:change={() => toggleEvent(event.id)}
                  class="w-4 h-4 mt-0.5 rounded border-slate-600 text-blue-600 focus:ring-blue-500"
                />
                <div>
                  <p class="text-sm font-medium">{event.name}</p>
                  <p class="text-xs text-slate-400">{event.description}</p>
                </div>
              </label>
            {/each}
          </div>
        </div>

        <!-- Enabled toggle -->
        <label class="flex items-center justify-between p-3 bg-slate-700/50 rounded-lg cursor-pointer">
          <div>
            <p class="font-medium">Enable Channel</p>
            <p class="text-xs text-slate-400">Receive notifications through this channel</p>
          </div>
          <button
            type="button"
            on:click={() => formEnabled = !formEnabled}
            class="relative"
          >
            {#if formEnabled}
              <ToggleRight class="w-8 h-8 text-green-400" />
            {:else}
              <ToggleLeft class="w-8 h-8 text-slate-500" />
            {/if}
          </button>
        </label>

        <!-- Test Result -->
        {#if testResult}
          <div class="p-3 rounded-lg {testResult.success ? 'bg-green-500/20 border border-green-500' : 'bg-red-500/20 border border-red-500'}">
            <p class="{testResult.success ? 'text-green-400' : 'text-red-400'} text-sm">
              {testResult.message}
            </p>
          </div>
        {/if}
      </div>

      <!-- Actions -->
      <div class="sticky bottom-0 bg-slate-800 p-4 border-t border-slate-700 flex gap-3">
        <button
          on:click={closeModal}
          class="flex-1 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
        >
          Cancel
        </button>
        <button
          on:click={handleSave}
          disabled={saving}
          class="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 rounded-lg transition-colors flex items-center justify-center gap-2"
        >
          {#if saving}
            <RefreshCw class="w-4 h-4 animate-spin" />
          {/if}
          {modalMode === "create" ? "Add Channel" : "Save Changes"}
        </button>
      </div>
    </div>
  </div>
{/if}
