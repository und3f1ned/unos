<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { getDisks, getPools, createPool, deletePool, startScrub, getScrubStatus, cancelScrub, type Disk, type Pool, type ScrubStatus } from '$lib/api/storage';
  import { HardDrive, Database, Plus, Trash2, X, AlertTriangle, RefreshCw, CheckCircle, XCircle, Loader2 } from 'lucide-svelte';

  let disks: Disk[] = [];
  let pools: Pool[] = [];
  let loading = true;
  let error = '';

  // Create Pool Modal state
  let showCreateModal = false;
  let saving = false;
  let poolForm = {
    name: '',
    devices: [] as string[],
    raid_level: 'single'
  };

  // Delete Pool Modal state
  let showDeleteModal = false;
  let deleteStep: 1 | 2 = 1;
  let poolToDelete: Pool | null = null;
  let deleteConfirmName = '';
  let deleting = false;

  // Scrub state
  let scrubStatuses: Record<string, ScrubStatus> = {};
  let scrubPollingIntervals: Record<string, ReturnType<typeof setInterval>> = {};
  let startingScrub: Record<string, boolean> = {};

  onMount(async () => {
    await loadData();
  });

  onDestroy(() => {
    // Clean up polling intervals
    Object.values(scrubPollingIntervals).forEach(interval => clearInterval(interval));
  });

  async function loadData() {
    loading = true;
    error = '';
    try {
      [disks, pools] = await Promise.all([getDisks(), getPools()]);
      // Load scrub status for each pool
      await loadScrubStatuses();
    } catch (e: any) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  async function loadScrubStatuses() {
    for (const pool of pools) {
      try {
        const status = await getScrubStatus(pool.name);
        scrubStatuses[pool.name] = status;
        scrubStatuses = scrubStatuses; // Trigger reactivity

        // If scrub is running, start polling
        if (status.status === 'running') {
          startScrubPolling(pool.name);
        }
      } catch {
        // Ignore errors for individual pool status
      }
    }
  }

  function startScrubPolling(poolName: string) {
    // Don't start if already polling
    if (scrubPollingIntervals[poolName]) return;

    scrubPollingIntervals[poolName] = setInterval(async () => {
      try {
        const status = await getScrubStatus(poolName);
        scrubStatuses[poolName] = status;
        scrubStatuses = scrubStatuses;

        // Stop polling if scrub is no longer running
        if (status.status !== 'running') {
          clearInterval(scrubPollingIntervals[poolName]);
          delete scrubPollingIntervals[poolName];
        }
      } catch {
        // Stop polling on error
        clearInterval(scrubPollingIntervals[poolName]);
        delete scrubPollingIntervals[poolName];
      }
    }, 2000);
  }

  async function handleStartScrub(poolName: string) {
    startingScrub[poolName] = true;
    startingScrub = startingScrub;
    error = '';

    try {
      const status = await startScrub(poolName);
      scrubStatuses[poolName] = status;
      scrubStatuses = scrubStatuses;
      startScrubPolling(poolName);
    } catch (e: any) {
      error = e.message;
    } finally {
      startingScrub[poolName] = false;
      startingScrub = startingScrub;
    }
  }

  async function handleCancelScrub(poolName: string) {
    try {
      const status = await cancelScrub(poolName);
      scrubStatuses[poolName] = status;
      scrubStatuses = scrubStatuses;

      // Stop polling
      if (scrubPollingIntervals[poolName]) {
        clearInterval(scrubPollingIntervals[poolName]);
        delete scrubPollingIntervals[poolName];
      }
    } catch (e: any) {
      error = e.message;
    }
  }

  function formatDuration(seconds: number): string {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  }

  function getScrubProgress(status: ScrubStatus): number {
    if (!status.bytes_scrubbed || !status.total_bytes || status.total_bytes === 0) {
      return 0;
    }
    return (status.bytes_scrubbed / status.total_bytes) * 100;
  }

  function formatBytes(bytes: number): string {
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (bytes >= 1024 && i < units.length - 1) {
      bytes /= 1024;
      i++;
    }
    return `${bytes.toFixed(1)} ${units[i]}`;
  }

  function getAvailableDisks(): Disk[] {
    const usedDevices = new Set(pools.flatMap(p => p.devices));
    return disks.filter(d => !d.mountpoint && !usedDevices.has(d.path));
  }

  function openCreateModal() {
    poolForm = { name: '', devices: [], raid_level: 'single' };
    showCreateModal = true;
  }

  function toggleDevice(path: string) {
    if (poolForm.devices.includes(path)) {
      poolForm.devices = poolForm.devices.filter(d => d !== path);
    } else {
      poolForm.devices = [...poolForm.devices, path];
    }
  }

  async function handleCreatePool() {
    if (!poolForm.name || poolForm.devices.length === 0) {
      error = 'Please provide a name and select at least one device';
      return;
    }

    saving = true;
    error = '';
    try {
      await createPool(poolForm);
      showCreateModal = false;
      await loadData();
    } catch (e: any) {
      error = e.message;
    } finally {
      saving = false;
    }
  }

  function openDeleteModal(pool: Pool) {
    poolToDelete = pool;
    deleteStep = 1;
    deleteConfirmName = '';
    showDeleteModal = true;
  }

  function closeDeleteModal() {
    showDeleteModal = false;
    poolToDelete = null;
    deleteStep = 1;
    deleteConfirmName = '';
  }

  function proceedToStep2() {
    deleteStep = 2;
  }

  async function handleDeletePool() {
    if (!poolToDelete || deleteConfirmName !== poolToDelete.name) return;

    deleting = true;
    error = '';
    try {
      await deletePool(poolToDelete.name);
      closeDeleteModal();
      await loadData();
    } catch (e: any) {
      error = e.message;
    } finally {
      deleting = false;
    }
  }
</script>

<div class="space-y-6">
  <div class="flex items-center justify-between">
    <h1 class="text-2xl font-bold">Storage</h1>
    <button
      on:click={openCreateModal}
      class="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors"
    >
      <Plus class="w-4 h-4" />
      Create Pool
    </button>
  </div>

  {#if error}
    <div class="bg-red-500/20 border border-red-500 text-red-400 px-4 py-3 rounded">
      {error}
    </div>
  {/if}

  {#if loading}
    <div class="text-center text-slate-500 py-8">Loading...</div>
  {:else}
    <!-- Disks Section -->
    <div class="bg-slate-800 rounded-lg p-4">
      <h2 class="text-lg font-semibold mb-4 flex items-center gap-2">
        <HardDrive class="w-5 h-5" />
        Physical Disks
      </h2>

      {#if disks.length === 0}
        <p class="text-slate-500">No disks found</p>
      {:else}
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-slate-400 border-b border-slate-700">
                <th class="pb-2">Device</th>
                <th class="pb-2">Model</th>
                <th class="pb-2">Serial</th>
                <th class="pb-2">Size</th>
                <th class="pb-2">Type</th>
                <th class="pb-2">Mount</th>
              </tr>
            </thead>
            <tbody>
              {#each disks as disk}
                <tr class="border-b border-slate-700/50">
                  <td class="py-2 font-mono">{disk.path}</td>
                  <td class="py-2">{disk.model || '-'}</td>
                  <td class="py-2 font-mono text-xs">{disk.serial || '-'}</td>
                  <td class="py-2">{formatBytes(disk.size)}</td>
                  <td class="py-2">
                    <span class="px-2 py-1 text-xs rounded {disk.rotational ? 'bg-yellow-500/20 text-yellow-400' : 'bg-green-500/20 text-green-400'}">
                      {disk.rotational ? 'HDD' : 'SSD'}
                    </span>
                  </td>
                  <td class="py-2 font-mono text-xs">{disk.mountpoint || '-'}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    </div>

    <!-- Pools Section -->
    <div class="bg-slate-800 rounded-lg p-4">
      <h2 class="text-lg font-semibold mb-4 flex items-center gap-2">
        <Database class="w-5 h-5" />
        Storage Pools
      </h2>

      {#if pools.length === 0}
        <p class="text-slate-500">No pools configured</p>
      {:else}
        <div class="space-y-4">
          {#each pools as pool}
            {@const scrubStatus = scrubStatuses[pool.name]}
            <div class="bg-slate-700/50 rounded-lg p-4">
              <div class="flex items-center justify-between mb-2">
                <h3 class="font-semibold">{pool.name}</h3>
                <div class="flex items-center gap-2">
                  {#if scrubStatus?.status === 'running'}
                    <button
                      on:click={() => handleCancelScrub(pool.name)}
                      class="flex items-center gap-1 px-3 py-1.5 text-sm bg-orange-600 hover:bg-orange-700 rounded-lg transition-colors"
                      title="Cancel scrub"
                    >
                      <XCircle class="w-4 h-4" />
                      Cancel Scrub
                    </button>
                  {:else}
                    <button
                      on:click={() => handleStartScrub(pool.name)}
                      disabled={startingScrub[pool.name]}
                      class="flex items-center gap-1 px-3 py-1.5 text-sm bg-slate-600 hover:bg-slate-500 disabled:bg-slate-700 disabled:cursor-not-allowed rounded-lg transition-colors"
                      title="Start data integrity check"
                    >
                      {#if startingScrub[pool.name]}
                        <Loader2 class="w-4 h-4 animate-spin" />
                      {:else}
                        <RefreshCw class="w-4 h-4" />
                      {/if}
                      Scrub
                    </button>
                  {/if}
                  <button
                    on:click={() => openDeleteModal(pool)}
                    class="p-2 text-red-400 hover:text-red-300 transition-colors"
                    title="Delete pool"
                  >
                    <Trash2 class="w-4 h-4" />
                  </button>
                </div>
              </div>

              <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                <div>
                  <span class="text-slate-400">RAID:</span>
                  <span class="ml-2">{pool.raid_level || 'single'}</span>
                </div>
                <div>
                  <span class="text-slate-400">Used:</span>
                  <span class="ml-2">{formatBytes(pool.used_size)} / {formatBytes(pool.total_size)}</span>
                </div>
                <div>
                  <span class="text-slate-400">Devices:</span>
                  <span class="ml-2">{pool.devices.length}</span>
                </div>
                <div>
                  <span class="text-slate-400">Mount:</span>
                  <span class="ml-2 font-mono text-xs">{pool.mountpoint || '-'}</span>
                </div>
              </div>

              {#if pool.total_size > 0}
                <div class="mt-3 h-2 bg-slate-600 rounded-full overflow-hidden">
                  <div
                    class="h-full bg-blue-500 transition-all"
                    style="width: {(pool.used_size / pool.total_size * 100).toFixed(1)}%"
                  ></div>
                </div>
              {/if}

              <!-- Scrub Status -->
              {#if scrubStatus}
                <div class="mt-3 pt-3 border-t border-slate-600">
                  {#if scrubStatus.status === 'running'}
                    <div class="space-y-2">
                      <div class="flex items-center justify-between text-sm">
                        <span class="flex items-center gap-2 text-blue-400">
                          <Loader2 class="w-4 h-4 animate-spin" />
                          Scrub in progress
                        </span>
                        <span class="text-slate-400">
                          {#if scrubStatus.duration}
                            {formatDuration(scrubStatus.duration)}
                          {/if}
                        </span>
                      </div>
                      {#if scrubStatus.total_bytes && scrubStatus.bytes_scrubbed}
                        <div class="h-2 bg-slate-600 rounded-full overflow-hidden">
                          <div
                            class="h-full bg-blue-500 transition-all"
                            style="width: {getScrubProgress(scrubStatus).toFixed(1)}%"
                          ></div>
                        </div>
                        <div class="flex justify-between text-xs text-slate-400">
                          <span>{formatBytes(scrubStatus.bytes_scrubbed)} / {formatBytes(scrubStatus.total_bytes)}</span>
                          <span>{getScrubProgress(scrubStatus).toFixed(1)}%</span>
                          {#if scrubStatus.rate}
                            <span>{formatBytes(scrubStatus.rate)}/s</span>
                          {/if}
                        </div>
                      {/if}
                    </div>
                  {:else if scrubStatus.status === 'finished' || scrubStatus.status === 'aborted'}
                    <div class="flex items-center justify-between text-sm">
                      <span class="flex items-center gap-2 {scrubStatus.errors > 0 ? 'text-red-400' : 'text-green-400'}">
                        {#if scrubStatus.errors > 0}
                          <XCircle class="w-4 h-4" />
                          Last scrub: {scrubStatus.errors} error(s)
                        {:else}
                          <CheckCircle class="w-4 h-4" />
                          Last scrub: No errors
                        {/if}
                      </span>
                      <span class="text-slate-400 text-xs">
                        {#if scrubStatus.last_run}
                          {scrubStatus.last_run}
                        {/if}
                      </span>
                    </div>
                  {/if}
                </div>
              {/if}
            </div>
          {/each}
        </div>
      {/if}
    </div>
  {/if}
</div>

<!-- Create Pool Modal -->
{#if showCreateModal}
  <div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
    <div class="bg-slate-800 rounded-lg p-6 w-full max-w-lg">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-xl font-bold">Create Storage Pool</h2>
        <button on:click={() => showCreateModal = false} class="text-slate-400 hover:text-white">
          <X class="w-5 h-5" />
        </button>
      </div>

      <form on:submit|preventDefault={handleCreatePool} class="space-y-4">
        <div>
          <label for="pool-name" class="block text-sm text-slate-400 mb-1">Pool Name</label>
          <input
            id="pool-name"
            type="text"
            bind:value={poolForm.name}
            class="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:border-blue-500 focus:outline-none"
            placeholder="data"
            required
          />
        </div>

        <div>
          <label for="raid-level" class="block text-sm text-slate-400 mb-1">RAID Level</label>
          <select
            id="raid-level"
            bind:value={poolForm.raid_level}
            class="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:border-blue-500 focus:outline-none"
          >
            <option value="single">Single (No RAID)</option>
            <option value="raid0">RAID 0 (Striping)</option>
            <option value="raid1">RAID 1 (Mirroring)</option>
            <option value="raid5">RAID 5 (requires 3+ disks)</option>
            <option value="raid6">RAID 6 (requires 4+ disks)</option>
            <option value="raid10">RAID 10 (requires 4+ disks)</option>
          </select>
        </div>

        <div>
          <label class="block text-sm text-slate-400 mb-2">Select Devices</label>
          {#if getAvailableDisks().length === 0}
            <p class="text-slate-500 text-sm">No available disks (all mounted or in use)</p>
          {:else}
            <div class="space-y-2 max-h-48 overflow-y-auto">
              {#each getAvailableDisks() as disk}
                <label class="flex items-center gap-3 p-2 bg-slate-700/50 rounded-lg cursor-pointer hover:bg-slate-700">
                  <input
                    type="checkbox"
                    checked={poolForm.devices.includes(disk.path)}
                    on:change={() => toggleDevice(disk.path)}
                    class="rounded"
                  />
                  <div class="flex-1">
                    <span class="font-mono text-sm">{disk.path}</span>
                    <span class="text-slate-400 ml-2">{formatBytes(disk.size)}</span>
                    {#if disk.model}
                      <span class="text-slate-500 ml-2 text-xs">{disk.model}</span>
                    {/if}
                  </div>
                </label>
              {/each}
            </div>
          {/if}
          {#if poolForm.devices.length > 0}
            <p class="text-sm text-slate-400 mt-2">
              Selected: {poolForm.devices.length} device(s)
            </p>
          {/if}
        </div>

        <div class="flex gap-3 pt-2">
          <button
            type="button"
            on:click={() => showCreateModal = false}
            class="flex-1 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={saving || poolForm.devices.length === 0}
            class="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 disabled:cursor-not-allowed rounded-lg"
          >
            {saving ? 'Creating...' : 'Create Pool'}
          </button>
        </div>
      </form>
    </div>
  </div>
{/if}

<!-- Delete Pool Modal -->
{#if showDeleteModal && poolToDelete}
  <div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
    <div class="bg-slate-800 rounded-lg p-6 w-full max-w-md">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-xl font-bold text-red-400 flex items-center gap-2">
          <AlertTriangle class="w-6 h-6" />
          Delete Pool
        </h2>
        <button on:click={closeDeleteModal} class="text-slate-400 hover:text-white">
          <X class="w-5 h-5" />
        </button>
      </div>

      {#if deleteStep === 1}
        <!-- Step 1: First confirmation -->
        <div class="space-y-4">
          <div class="bg-red-500/10 border border-red-500/50 rounded-lg p-4">
            <p class="text-red-400 font-medium mb-2">Warning!</p>
            <p class="text-slate-300 text-sm">
              You are about to delete the storage pool <strong class="text-white">"{poolToDelete.name}"</strong>.
            </p>
            <p class="text-slate-400 text-sm mt-2">
              This will unmount the pool. Data on the disks will remain intact but will no longer be accessible through UNOS.
            </p>
          </div>

          <div class="bg-slate-700/50 rounded-lg p-3 text-sm">
            <p class="text-slate-400">Pool details:</p>
            <ul class="mt-2 space-y-1 text-slate-300">
              <li>Size: {formatBytes(poolToDelete.total_size)}</li>
              <li>Used: {formatBytes(poolToDelete.used_size)}</li>
              <li>Devices: {poolToDelete.devices.length}</li>
              <li>Mount: {poolToDelete.mountpoint || 'Not mounted'}</li>
            </ul>
          </div>

          <div class="flex gap-3 pt-2">
            <button
              type="button"
              on:click={closeDeleteModal}
              class="flex-1 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg"
            >
              Cancel
            </button>
            <button
              type="button"
              on:click={proceedToStep2}
              class="flex-1 px-4 py-2 bg-red-600 hover:bg-red-700 rounded-lg"
            >
              Continue
            </button>
          </div>
        </div>
      {:else}
        <!-- Step 2: Type pool name to confirm -->
        <div class="space-y-4">
          <div class="bg-red-500/10 border border-red-500/50 rounded-lg p-4">
            <p class="text-red-400 font-medium mb-2">Final Confirmation</p>
            <p class="text-slate-300 text-sm">
              To confirm deletion, please type the pool name:
            </p>
            <p class="text-white font-mono text-lg mt-2 bg-slate-700 px-3 py-2 rounded">{poolToDelete.name}</p>
          </div>

          <div>
            <label for="confirm-name" class="block text-sm text-slate-400 mb-1">Type pool name to confirm</label>
            <input
              id="confirm-name"
              type="text"
              bind:value={deleteConfirmName}
              class="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:border-red-500 focus:outline-none"
              placeholder="Enter pool name"
              autocomplete="off"
            />
          </div>

          <div class="flex gap-3 pt-2">
            <button
              type="button"
              on:click={() => deleteStep = 1}
              class="flex-1 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg"
            >
              Back
            </button>
            <button
              type="button"
              on:click={handleDeletePool}
              disabled={deleting || deleteConfirmName !== poolToDelete.name}
              class="flex-1 px-4 py-2 bg-red-600 hover:bg-red-700 disabled:bg-red-800 disabled:cursor-not-allowed rounded-lg"
            >
              {deleting ? 'Deleting...' : 'Delete Pool'}
            </button>
          </div>
        </div>
      {/if}
    </div>
  </div>
{/if}
