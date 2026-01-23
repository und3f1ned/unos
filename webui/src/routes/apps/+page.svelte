<script lang="ts">
  import { onMount } from "svelte";
  import {
    getAppCatalog,
    getInstalledApps,
    installApp,
    uninstallApp,
    startApp,
    stopApp,
    type AppTemplate,
    type InstalledApp,
  } from "$lib/api/apps";
  import {
    Package,
    Download,
    Play,
    Square,
    Trash2,
    ExternalLink,
    Search,
    RefreshCw,
    Loader2,
  } from "lucide-svelte";

  let catalog: AppTemplate[] = [];
  let installed: InstalledApp[] = [];
  let loading = true;
  let installing: string | null = null;
  let searchQuery = "";
  let selectedCategory = "";
  let activeTab: "catalog" | "installed" = "catalog";

  const categories = [
    { value: "", label: "All" },
    { value: "media", label: "Media" },
    { value: "productivity", label: "Productivity" },
    { value: "download", label: "Downloads" },
    { value: "backup", label: "Backup" },
    { value: "monitoring", label: "Monitoring" },
    { value: "network", label: "Network" },
    { value: "development", label: "Development" },
    { value: "security", label: "Security" },
  ];

  onMount(async () => {
    await refresh();
  });

  async function refresh() {
    loading = true;
    try {
      [catalog, installed] = await Promise.all([
        getAppCatalog(selectedCategory || undefined, searchQuery || undefined),
        getInstalledApps(),
      ]);
    } catch (e) {
      console.error(e);
    }
    loading = false;
  }

  async function handleInstall(app: AppTemplate) {
    installing = app.id;
    try {
      await installApp({ template_id: app.id });
      await refresh();
    } catch (e: any) {
      alert(e.message || "Install failed");
    }
    installing = null;
  }

  async function handleUninstall(app: InstalledApp) {
    if (!confirm(`Uninstall ${app.name}?`)) return;
    try {
      await uninstallApp(app.container_id);
      await refresh();
    } catch (e: any) {
      alert(e.message || "Uninstall failed");
    }
  }

  async function handleStart(app: InstalledApp) {
    try {
      await startApp(app.container_id);
      await refresh();
    } catch (e: any) {
      alert(e.message || "Start failed");
    }
  }

  async function handleStop(app: InstalledApp) {
    try {
      await stopApp(app.container_id);
      await refresh();
    } catch (e: any) {
      alert(e.message || "Stop failed");
    }
  }

  function isInstalled(appId: string): boolean {
    return installed.some((i) => i.template_id === appId);
  }

  function getInstalledInfo(appId: string): InstalledApp | undefined {
    return installed.find((i) => i.template_id === appId);
  }

  $: filteredCatalog = catalog;
</script>

<div class="p-6">
  <div class="flex items-center justify-between mb-6">
    <h1 class="text-2xl font-bold flex items-center gap-2">
      <Package class="w-7 h-7" />
      App Store
    </h1>
    <button
      on:click={refresh}
      class="p-2 rounded-lg bg-slate-700 hover:bg-slate-600 transition"
      disabled={loading}
    >
      <RefreshCw class={`w-5 h-5 ${loading ? 'animate-spin' : ''}`} />
    </button>
  </div>

  <!-- Tabs -->
  <div class="flex gap-4 mb-6 border-b border-slate-700">
    <button
      class="pb-2 px-1 {activeTab === 'catalog' ? 'border-b-2 border-blue-500 text-blue-400' : 'text-slate-400'}"
      on:click={() => (activeTab = "catalog")}
    >
      Catalog ({catalog.length})
    </button>
    <button
      class="pb-2 px-1 {activeTab === 'installed' ? 'border-b-2 border-blue-500 text-blue-400' : 'text-slate-400'}"
      on:click={() => (activeTab = "installed")}
    >
      Installed ({installed.length})
    </button>
  </div>

  {#if activeTab === "catalog"}
    <!-- Search & Filter -->
    <div class="flex gap-4 mb-6">
      <div class="relative flex-1">
        <Search class="w-5 h-5 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <input
          type="text"
          placeholder="Search apps..."
          bind:value={searchQuery}
          on:input={() => refresh()}
          class="w-full pl-10 pr-4 py-2 bg-slate-800 rounded-lg border border-slate-700 focus:border-blue-500 focus:outline-none"
        />
      </div>
      <select
        bind:value={selectedCategory}
        on:change={() => refresh()}
        class="px-4 py-2 bg-slate-800 rounded-lg border border-slate-700 focus:border-blue-500 focus:outline-none"
      >
        {#each categories as cat}
          <option value={cat.value}>{cat.label}</option>
        {/each}
      </select>
    </div>

    <!-- App Grid -->
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
      {#each filteredCatalog as app}
        {@const installedInfo = getInstalledInfo(app.id)}
        <div class="bg-slate-800 rounded-xl p-4 border border-slate-700">
          <div class="flex items-start gap-3 mb-3">
            <span class="text-3xl">{app.icon}</span>
            <div class="flex-1 min-w-0">
              <h3 class="font-semibold truncate">{app.name}</h3>
              <p class="text-xs text-slate-400 capitalize">{app.category}</p>
            </div>
          </div>
          <p class="text-sm text-slate-400 mb-4 line-clamp-2">{app.description}</p>
          <div class="flex items-center gap-2">
            {#if installedInfo}
              <span
                class="px-2 py-1 rounded text-xs {installedInfo.status === 'running' ? 'bg-green-500/20 text-green-400' : 'bg-slate-600 text-slate-300'}"
              >
                {installedInfo.status}
              </span>
              {#if installedInfo.web_port}
                <a
                  href="http://{window.location.hostname}:{installedInfo.web_port}"
                  target="_blank"
                  class="p-1.5 rounded bg-blue-600 hover:bg-blue-500"
                >
                  <ExternalLink class="w-4 h-4" />
                </a>
              {/if}
            {:else}
              <button
                on:click={() => handleInstall(app)}
                disabled={installing === app.id}
                class="flex-1 flex items-center justify-center gap-2 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 transition"
              >
                {#if installing === app.id}
                  <Loader2 class="w-4 h-4 animate-spin" />
                  Installing...
                {:else}
                  <Download class="w-4 h-4" />
                  Install
                {/if}
              </button>
            {/if}
          </div>
        </div>
      {/each}
    </div>
  {:else}
    <!-- Installed Apps -->
    <div class="space-y-3">
      {#each installed as app}
        {@const template = catalog.find((c) => c.id === app.template_id)}
        <div class="bg-slate-800 rounded-xl p-4 border border-slate-700 flex items-center gap-4">
          <span class="text-3xl">{template?.icon || "📦"}</span>
          <div class="flex-1 min-w-0">
            <h3 class="font-semibold">{app.name}</h3>
            <p class="text-sm text-slate-400">{template?.description || app.template_id}</p>
          </div>
          <span
            class="px-3 py-1 rounded-full text-sm {app.status === 'running' ? 'bg-green-500/20 text-green-400' : 'bg-slate-600 text-slate-300'}"
          >
            {app.status}
          </span>
          <div class="flex items-center gap-2">
            {#if app.web_port}
              <a
                href="http://{window.location.hostname}:{app.web_port}"
                target="_blank"
                class="p-2 rounded-lg bg-slate-700 hover:bg-slate-600"
                title="Open"
              >
                <ExternalLink class="w-5 h-5" />
              </a>
            {/if}
            {#if app.status === "running"}
              <button
                on:click={() => handleStop(app)}
                class="p-2 rounded-lg bg-slate-700 hover:bg-orange-600"
                title="Stop"
              >
                <Square class="w-5 h-5" />
              </button>
            {:else}
              <button
                on:click={() => handleStart(app)}
                class="p-2 rounded-lg bg-slate-700 hover:bg-green-600"
                title="Start"
              >
                <Play class="w-5 h-5" />
              </button>
            {/if}
            <button
              on:click={() => handleUninstall(app)}
              class="p-2 rounded-lg bg-slate-700 hover:bg-red-600"
              title="Uninstall"
            >
              <Trash2 class="w-5 h-5" />
            </button>
          </div>
        </div>
      {/each}
      {#if installed.length === 0}
        <div class="text-center py-12 text-slate-400">
          <Package class="w-12 h-12 mx-auto mb-3 opacity-50" />
          <p>No apps installed yet</p>
          <button
            on:click={() => (activeTab = "catalog")}
            class="mt-2 text-blue-400 hover:underline"
          >
            Browse catalog
          </button>
        </div>
      {/if}
    </div>
  {/if}
</div>
