<script lang="ts">
  import { onMount } from "svelte";
  import { getSystemInfo, type SystemInfo } from "$lib/api/system";
  import { getCurrentVersion, type CurrentVersion } from "$lib/api/updates";
  import { user } from "$lib/stores/auth";
  import { Server, User, Package } from "lucide-svelte";

  let systemInfo: SystemInfo | null = null;
  let currentVersion: CurrentVersion | null = null;
  let loading = true;

  onMount(async () => {
    try {
      const [info, version] = await Promise.all([
        getSystemInfo(),
        getCurrentVersion(),
      ]);
      systemInfo = info;
      currentVersion = version;
    } catch (e) {
      console.error("Failed to load system info:", e);
    } finally {
      loading = false;
    }
  });

  function formatUptime(bootTime: number): string {
    const now = Date.now() / 1000;
    const seconds = Math.floor(now - bootTime);
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    if (days > 0) return days + "d " + hours + "h " + mins + "m";
    if (hours > 0) return hours + "h " + mins + "m";
    return mins + "m";
  }
</script>

<div class="space-y-6">
  <h2 class="text-xl font-semibold">General Settings</h2>

  <!-- System Info -->
  <div class="bg-slate-800 rounded-lg p-4">
    <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
      <Server class="w-5 h-5" />
      System Information
    </h3>

    {#if loading}
      <p class="text-slate-500">Loading...</p>
    {:else if systemInfo}
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
        <div>
          <span class="text-slate-400">Hostname:</span>
          <span class="ml-2 font-medium">{systemInfo.hostname}</span>
        </div>
        <div>
          <span class="text-slate-400">Operating System:</span>
          <span class="ml-2">{systemInfo.os}</span>
        </div>
        <div>
          <span class="text-slate-400">Kernel:</span>
          <span class="ml-2 font-mono text-xs">{systemInfo.kernel}</span>
        </div>
        <div>
          <span class="text-slate-400">Architecture:</span>
          <span class="ml-2">{systemInfo.architecture}</span>
        </div>
        <div>
          <span class="text-slate-400">Uptime:</span>
          <span class="ml-2">{formatUptime(systemInfo.uptime)}</span>
        </div>
      </div>
    {:else}
      <p class="text-slate-500">Failed to load system info</p>
    {/if}
  </div>

  <!-- UNOS Version -->
  <div class="bg-slate-800 rounded-lg p-4">
    <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
      <Package class="w-5 h-5 text-blue-400" />
      UNOS Version
    </h3>

    {#if loading}
      <p class="text-slate-500">Loading...</p>
    {:else if currentVersion}
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
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
      </div>
      <a
        href="#updates"
        class="inline-block mt-3 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors text-sm"
      >
        Check for Updates
      </a>
    {:else}
      <p class="text-slate-500">Version info unavailable</p>
    {/if}
  </div>

  <!-- User Account -->
  <div class="bg-slate-800 rounded-lg p-4">
    <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
      <User class="w-5 h-5" />
      Current User
    </h3>

    {#if $user}
      <div class="space-y-3">
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
          <div>
            <span class="text-slate-400">Username:</span>
            <span class="ml-2 font-medium">{$user.username}</span>
          </div>
          <div>
            <span class="text-slate-400">Role:</span>
            <span class="ml-2">
              {#if $user.is_admin}
                <span class="px-2 py-0.5 bg-blue-500/20 text-blue-400 rounded text-xs">Administrator</span>
              {:else}
                <span class="px-2 py-0.5 bg-slate-500/20 text-slate-400 rounded text-xs">User</span>
              {/if}
            </span>
          </div>
        </div>
        <a
          href="/users"
          class="inline-block px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors text-sm"
        >
          Manage Users
        </a>
      </div>
    {:else}
      <p class="text-slate-500">Not logged in</p>
    {/if}
  </div>
</div>
