<script lang="ts">
  import { onMount } from 'svelte';
  import { getSystemInfo, type SystemInfo } from '$lib/api/system';
  import { user } from '$lib/stores/auth';
  import { Server, User, Shield } from 'lucide-svelte';

  let systemInfo: SystemInfo | null = null;
  let loading = true;

  onMount(async () => {
    try {
      systemInfo = await getSystemInfo();
    } catch (e) {
      console.error('Failed to load system info:', e);
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
    if (days > 0) return days + 'd ' + hours + 'h ' + mins + 'm';
    if (hours > 0) return hours + 'h ' + mins + 'm';
    return mins + 'm';
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

  <!-- Security -->
  <div class="bg-slate-800 rounded-lg p-4">
    <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
      <Shield class="w-5 h-5" />
      Security
    </h3>

    <div class="space-y-4">
      <div class="flex items-center justify-between">
        <div>
          <p class="font-medium">Two-Factor Authentication</p>
          <p class="text-sm text-slate-400">Add an extra layer of security</p>
        </div>
        <span class="px-3 py-1 bg-slate-700 text-slate-400 rounded-lg text-sm">
          Coming Soon
        </span>
      </div>

      <div class="flex items-center justify-between">
        <div>
          <p class="font-medium">Firewall</p>
          <p class="text-sm text-slate-400">Configure network firewall rules</p>
        </div>
        <span class="px-3 py-1 bg-slate-700 text-slate-400 rounded-lg text-sm">
          Coming Soon
        </span>
      </div>

      <div class="flex items-center justify-between">
        <div>
          <p class="font-medium">SSL/TLS Certificates</p>
          <p class="text-sm text-slate-400">Manage HTTPS certificates</p>
        </div>
        <span class="px-3 py-1 bg-slate-700 text-slate-400 rounded-lg text-sm">
          Coming Soon
        </span>
      </div>
    </div>
  </div>
</div>
