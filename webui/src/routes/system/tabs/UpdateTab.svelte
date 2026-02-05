<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import {
    getCurrentVersion,
    checkForUpdates,
    installUpdate,
    getUpdateProgress,
    rollbackUpdate,
    resetUpdateStatus,
    getUpdateHistory,
    getUpdateConfig,
    saveUpdateConfig,
    type CurrentVersion,
    type UpdateCheck,
    type UpdateProgress,
    type UpdateHistoryEntry,
    type UpdateConfig,
    type UpdateStatus,
  } from "$lib/api/updates";
  import {
    Download,
    RefreshCw,
    CheckCircle,
    AlertTriangle,
    Clock,
    History,
    Settings,
    ArrowUpCircle,
    RotateCcw,
    ExternalLink,
    Shield,
    Package,
    Info,
  } from "lucide-svelte";

  let currentVersion: CurrentVersion | null = null;
  let updateCheck: UpdateCheck | null = null;
  let progress: UpdateProgress | null = null;
  let history: UpdateHistoryEntry[] = [];
  let config: UpdateConfig | null = null;

  let loading = true;
  let checking = false;
  let installing = false;
  let showHistory = false;
  let showSettings = false;

  // Polling interval for update progress
  let progressInterval: ReturnType<typeof setInterval> | null = null;

  // Status messages
  const statusMessages: Record<UpdateStatus, string> = {
    idle: "Ready",
    checking: "Checking for updates...",
    downloading: "Downloading update...",
    verifying: "Verifying download...",
    backing_up: "Creating backup...",
    installing: "Installing update...",
    restarting: "Restarting services...",
    completed: "Update complete!",
    failed: "Update failed",
    rollback: "Rolling back...",
  };

  onMount(async () => {
    await loadData();
    loading = false;
  });

  onDestroy(() => {
    stopProgressPolling();
  });

  async function loadData() {
    try {
      const [version, progressData, historyData, configData] = await Promise.all([
        getCurrentVersion(),
        getUpdateProgress(),
        getUpdateHistory(),
        getUpdateConfig(),
      ]);
      currentVersion = version;
      progress = progressData;
      history = historyData;
      config = configData;

      // Start polling if update is in progress
      if (isUpdateInProgress(progress.status)) {
        startProgressPolling();
      }
    } catch (e) {
      console.error("Failed to load update data:", e);
    }
  }

  function isUpdateInProgress(status: UpdateStatus): boolean {
    return !["idle", "completed", "failed"].includes(status);
  }

  function startProgressPolling() {
    if (progressInterval) return;
    progressInterval = setInterval(async () => {
      try {
        progress = await getUpdateProgress();
        if (!isUpdateInProgress(progress.status)) {
          stopProgressPolling();
          // Reload version info after update completes
          currentVersion = await getCurrentVersion();
          history = await getUpdateHistory();
        }
      } catch (e) {
        console.error("Failed to poll progress:", e);
      }
    }, 2000);
  }

  function stopProgressPolling() {
    if (progressInterval) {
      clearInterval(progressInterval);
      progressInterval = null;
    }
  }

  async function handleCheck() {
    checking = true;
    try {
      updateCheck = await checkForUpdates(config?.include_prereleases ?? false);
    } catch (e) {
      console.error("Failed to check for updates:", e);
    }
    checking = false;
  }

  async function handleInstall() {
    if (!updateCheck?.release_info) return;

    installing = true;
    try {
      await installUpdate(updateCheck.release_info.tag_name);
      // Start polling for progress
      progress = await getUpdateProgress();
      startProgressPolling();
    } catch (e: any) {
      console.error("Failed to start update:", e);
      alert(e.message || "Failed to start update");
    }
    installing = false;
  }

  async function handleRollback() {
    if (!confirm("Are you sure you want to rollback to the previous version?")) return;

    try {
      await rollbackUpdate();
      progress = await getUpdateProgress();
      startProgressPolling();
    } catch (e: any) {
      console.error("Failed to rollback:", e);
      alert(e.message || "Failed to rollback");
    }
  }

  async function handleReset() {
    try {
      await resetUpdateStatus();
      progress = await getUpdateProgress();
    } catch (e: any) {
      console.error("Failed to reset:", e);
      alert(e.message || "Failed to reset status");
    }
  }

  async function handleSaveConfig() {
    if (!config) return;
    try {
      await saveUpdateConfig(config);
      showSettings = false;
    } catch (e: any) {
      console.error("Failed to save config:", e);
      alert(e.message || "Failed to save configuration");
    }
  }

  function formatDate(date: string): string {
    return new Date(date).toLocaleString();
  }

  function getStatusColor(status: UpdateStatus): string {
    switch (status) {
      case "completed":
        return "text-green-400";
      case "failed":
        return "text-red-400";
      case "idle":
        return "text-slate-400";
      default:
        return "text-blue-400";
    }
  }

  function getHistoryStatusColor(status: string): string {
    switch (status) {
      case "success":
        return "text-green-400";
      case "failed":
        return "text-red-400";
      default:
        return "text-slate-400";
    }
  }
</script>

<div class="space-y-6">
  <div class="flex items-center justify-between">
    <h2 class="text-xl font-bold">Software Updates</h2>
    <div class="flex gap-2">
      <button
        on:click={() => (showHistory = !showHistory)}
        class="flex items-center gap-2 px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm"
      >
        <History class="w-4 h-4" />
        History
      </button>
      <button
        on:click={() => (showSettings = !showSettings)}
        class="flex items-center gap-2 px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm"
      >
        <Settings class="w-4 h-4" />
        Settings
      </button>
    </div>
  </div>

  {#if loading}
    <div class="flex justify-center py-8">
      <RefreshCw class="w-6 h-6 animate-spin text-blue-500" />
    </div>
  {:else}
    <!-- Current Version -->
    <div class="bg-slate-800 rounded-lg p-4">
      <h3 class="text-lg font-semibold mb-3 flex items-center gap-2">
        <Package class="w-5 h-5 text-blue-400" />
        Current Version
      </h3>
      {#if currentVersion}
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          <div>
            <span class="text-slate-400">Version:</span>
            <span class="ml-2 font-mono font-medium">{currentVersion.version}</span>
            {#if currentVersion.is_development}
              <span class="ml-2 px-1.5 py-0.5 bg-yellow-500/20 text-yellow-400 rounded text-xs">dev</span>
            {/if}
          </div>
          <div>
            <span class="text-slate-400">Commit:</span>
            <span class="ml-2 font-mono text-xs">{currentVersion.commit_hash}</span>
          </div>
          {#if currentVersion.install_date}
            <div>
              <span class="text-slate-400">Installed:</span>
              <span class="ml-2">{formatDate(currentVersion.install_date)}</span>
            </div>
          {/if}
        </div>
      {/if}
    </div>

    <!-- Update Status / Check -->
    <div class="bg-slate-800 rounded-lg p-4">
      <h3 class="text-lg font-semibold mb-3 flex items-center gap-2">
        <ArrowUpCircle class="w-5 h-5 text-green-400" />
        Update Status
      </h3>

      {#if progress && isUpdateInProgress(progress.status)}
        <!-- Update in progress -->
        <div class="space-y-4">
          <div class="flex items-center gap-3">
            <RefreshCw class="w-5 h-5 animate-spin text-blue-400" />
            <span class={getStatusColor(progress.status)}>
              {statusMessages[progress.status]}
            </span>
          </div>
          <div class="bg-slate-700 rounded-full h-3 overflow-hidden">
            <div
              class="h-full bg-blue-500 transition-all duration-500"
              style="width: {progress.progress_percent}%"
            ></div>
          </div>
          <p class="text-sm text-slate-400">{progress.message}</p>
        </div>
      {:else if progress?.status === "completed"}
        <!-- Update completed -->
        <div class="flex items-center gap-3 text-green-400 mb-4">
          <CheckCircle class="w-6 h-6" />
          <span>Update completed successfully!</span>
        </div>
        <div class="flex gap-2">
          <button
            on:click={handleReset}
            class="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm"
          >
            Dismiss
          </button>
          {#if progress.can_rollback}
            <button
              on:click={handleRollback}
              class="flex items-center gap-2 px-4 py-2 bg-yellow-600 hover:bg-yellow-500 rounded-lg text-sm"
            >
              <RotateCcw class="w-4 h-4" />
              Rollback
            </button>
          {/if}
        </div>
      {:else if progress?.status === "failed"}
        <!-- Update failed -->
        <div class="space-y-4">
          <div class="flex items-center gap-3 text-red-400">
            <AlertTriangle class="w-6 h-6" />
            <span>Update failed</span>
          </div>
          {#if progress.error}
            <div class="p-3 bg-red-500/10 border border-red-500/30 rounded-lg">
              <p class="text-sm text-red-400">{progress.error}</p>
            </div>
          {/if}
          <div class="flex gap-2">
            <button
              on:click={handleReset}
              class="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm"
            >
              Dismiss
            </button>
            {#if progress.can_rollback}
              <button
                on:click={handleRollback}
                class="flex items-center gap-2 px-4 py-2 bg-yellow-600 hover:bg-yellow-500 rounded-lg text-sm"
              >
                <RotateCcw class="w-4 h-4" />
                Rollback
              </button>
            {/if}
          </div>
        </div>
      {:else}
        <!-- Idle - show check/update UI -->
        {#if updateCheck}
          {#if updateCheck.update_available && updateCheck.release_info}
            <!-- Update available -->
            <div class="space-y-4">
              <div class="flex items-center gap-3 text-green-400">
                <Download class="w-6 h-6" />
                <span>Update available: {updateCheck.latest_version}</span>
              </div>

              <div class="p-4 bg-slate-700 rounded-lg">
                <div class="flex items-center justify-between mb-2">
                  <h4 class="font-semibold">{updateCheck.release_info.name || updateCheck.release_info.tag_name}</h4>
                  <a
                    href={updateCheck.release_info.html_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    class="flex items-center gap-1 text-sm text-blue-400 hover:text-blue-300"
                  >
                    View on GitHub <ExternalLink class="w-3 h-3" />
                  </a>
                </div>
                <p class="text-xs text-slate-400 mb-3">
                  Released: {formatDate(updateCheck.release_info.published_at)}
                  {#if updateCheck.release_info.prerelease}
                    <span class="ml-2 px-1.5 py-0.5 bg-yellow-500/20 text-yellow-400 rounded">Pre-release</span>
                  {/if}
                </p>
                {#if updateCheck.release_info.body}
                  <div class="text-sm text-slate-300 whitespace-pre-wrap max-h-48 overflow-y-auto scrollbar-thin">
                    {updateCheck.release_info.body}
                  </div>
                {/if}
              </div>

              <div class="flex items-center gap-3">
                <div class="flex items-center gap-2 text-sm text-slate-400">
                  <Shield class="w-4 h-4" />
                  <span>Secure update with backup</span>
                </div>
              </div>

              <div class="flex gap-2">
                <button
                  on:click={handleCheck}
                  class="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm"
                  disabled={checking}
                >
                  {#if checking}
                    <RefreshCw class="w-4 h-4 animate-spin inline mr-2" />
                  {/if}
                  Check Again
                </button>
                <button
                  on:click={handleInstall}
                  class="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm"
                  disabled={installing}
                >
                  {#if installing}
                    <RefreshCw class="w-4 h-4 animate-spin" />
                  {:else}
                    <Download class="w-4 h-4" />
                  {/if}
                  Install Update
                </button>
              </div>
            </div>
          {:else}
            <!-- Up to date -->
            <div class="space-y-4">
              <div class="flex items-center gap-3 text-green-400">
                <CheckCircle class="w-6 h-6" />
                <span>You're running the latest version</span>
              </div>
              <p class="text-sm text-slate-400">
                Last checked: {formatDate(updateCheck.checked_at)}
              </p>
              <button
                on:click={handleCheck}
                class="flex items-center gap-2 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm"
                disabled={checking}
              >
                {#if checking}
                  <RefreshCw class="w-4 h-4 animate-spin" />
                {:else}
                  <RefreshCw class="w-4 h-4" />
                {/if}
                Check for Updates
              </button>
            </div>
          {/if}
        {:else}
          <!-- No check yet -->
          <div class="space-y-4">
            <div class="flex items-center gap-3 text-slate-400">
              <Info class="w-6 h-6" />
              <span>Check for available updates</span>
            </div>
            <button
              on:click={handleCheck}
              class="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm"
              disabled={checking}
            >
              {#if checking}
                <RefreshCw class="w-4 h-4 animate-spin" />
              {:else}
                <RefreshCw class="w-4 h-4" />
              {/if}
              Check for Updates
            </button>
          </div>
        {/if}
      {/if}
    </div>

    <!-- Update History -->
    {#if showHistory}
      <div class="bg-slate-800 rounded-lg p-4">
        <h3 class="text-lg font-semibold mb-3 flex items-center gap-2">
          <History class="w-5 h-5 text-purple-400" />
          Update History
        </h3>
        {#if history.length === 0}
          <p class="text-slate-400 text-sm">No update history yet</p>
        {:else}
          <div class="space-y-2">
            {#each history as entry}
              <div class="flex items-center justify-between p-3 bg-slate-700 rounded-lg">
                <div class="flex items-center gap-3">
                  {#if entry.status === "success"}
                    <CheckCircle class="w-5 h-5 text-green-400" />
                  {:else}
                    <AlertTriangle class="w-5 h-5 text-red-400" />
                  {/if}
                  <div>
                    <p class="text-sm">
                      <span class="text-slate-400">{entry.from_version}</span>
                      <span class="mx-2">→</span>
                      <span class="font-medium">{entry.to_version}</span>
                    </p>
                    <p class="text-xs text-slate-400 flex items-center gap-1">
                      <Clock class="w-3 h-3" />
                      {formatDate(entry.started_at)}
                    </p>
                  </div>
                </div>
                <span class={`text-sm ${getHistoryStatusColor(entry.status)}`}>
                  {entry.status}
                </span>
              </div>
            {/each}
          </div>
        {/if}
      </div>
    {/if}

    <!-- Settings -->
    {#if showSettings && config}
      <div class="bg-slate-800 rounded-lg p-4">
        <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
          <Settings class="w-5 h-5 text-slate-400" />
          Update Settings
        </h3>
        <div class="space-y-4">
          <label class="flex items-center gap-3">
            <input
              type="checkbox"
              bind:checked={config.include_prereleases}
              class="w-4 h-4 rounded border-slate-600 bg-slate-700 text-blue-500 focus:ring-blue-500"
            />
            <span class="text-sm">Include pre-release versions</span>
          </label>

          <label class="flex items-center gap-3">
            <input
              type="checkbox"
              bind:checked={config.auto_check}
              class="w-4 h-4 rounded border-slate-600 bg-slate-700 text-blue-500 focus:ring-blue-500"
            />
            <span class="text-sm">Automatically check for updates</span>
          </label>

          {#if config.auto_check}
            <div class="ml-7">
              <label class="block text-sm text-slate-400 mb-1">Check interval (hours)</label>
              <input
                type="number"
                bind:value={config.check_interval_hours}
                min="1"
                max="168"
                class="w-24 px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:border-blue-500 focus:outline-none text-sm"
              />
            </div>
          {/if}

          <div class="pt-2">
            <button
              on:click={handleSaveConfig}
              class="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm"
            >
              Save Settings
            </button>
          </div>
        </div>
      </div>
    {/if}
  {/if}
</div>
