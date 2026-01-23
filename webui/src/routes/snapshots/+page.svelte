<script lang="ts">
  import { onMount } from "svelte";
  import {
    getSubvolumes,
    getSnapshots,
    createSnapshot,
    deleteSnapshot,
    restoreSnapshot,
    type SnapshotInfo,
    type Subvolume,
  } from "$lib/api/snapshots";
  import {
    Archive,
    Plus,
    Trash2,
    RotateCcw,
    RefreshCw,
    HardDrive,
    Calendar,
    Database,
  } from "lucide-svelte";

  let subvolumes: Subvolume[] = [];
  let snapshots: SnapshotInfo[] = [];
  let selectedSubvolume = "";
  let loading = true;
  let creating = false;
  let newSnapshotName = "";
  let showCreateDialog = false;

  onMount(async () => {
    await refresh();
  });

  async function refresh() {
    loading = true;
    try {
      subvolumes = await getSubvolumes();
      if (subvolumes.length > 0 && !selectedSubvolume) {
        selectedSubvolume = subvolumes[0].path;
      }
      snapshots = await getSnapshots(selectedSubvolume || undefined);
    } catch (e) {
      console.error(e);
    }
    loading = false;
  }

  async function handleCreate() {
    if (!selectedSubvolume) return;
    creating = true;
    try {
      await createSnapshot(selectedSubvolume, newSnapshotName || undefined);
      newSnapshotName = "";
      showCreateDialog = false;
      await refresh();
    } catch (e: any) {
      alert(e.message || "Failed to create snapshot");
    }
    creating = false;
  }

  async function handleDelete(snap: SnapshotInfo) {
    if (!confirm(`Delete snapshot "${snap.name}"?`)) return;
    try {
      await deleteSnapshot(snap.id, snap.subvolume);
      await refresh();
    } catch (e: any) {
      alert(e.message || "Failed to delete snapshot");
    }
  }

  async function handleRestore(snap: SnapshotInfo) {
    if (!confirm(`Restore from snapshot "${snap.name}"? This will create a new directory with the restored data.`)) return;
    try {
      const result = await restoreSnapshot(snap.path);
      alert(`Restored to: ${result.target}`);
      await refresh();
    } catch (e: any) {
      alert(e.message || "Failed to restore snapshot");
    }
  }

  function formatDate(dateStr: string): string {
    if (!dateStr) return "-";
    try {
      return new Date(dateStr).toLocaleString();
    } catch {
      return dateStr;
    }
  }
</script>

<div class="p-6">
  <div class="flex items-center justify-between mb-6">
    <h1 class="text-2xl font-bold flex items-center gap-2">
      <Archive class="w-7 h-7" />
      Snapshots
    </h1>
    <div class="flex items-center gap-2">
      <button
        on:click={() => (showCreateDialog = true)}
        class="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg transition"
        disabled={!selectedSubvolume}
      >
        <Plus class="w-5 h-5" />
        Create Snapshot
      </button>
      <button
        on:click={refresh}
        class="p-2 rounded-lg bg-slate-700 hover:bg-slate-600 transition"
        disabled={loading}
      >
        <RefreshCw class={`w-5 h-5 ${loading ? 'animate-spin' : ''}`} />
      </button>
    </div>
  </div>

  <!-- Subvolume Selector -->
  <div class="mb-6">
    <label class="block text-sm text-slate-400 mb-2">Select Storage Pool</label>
    <div class="flex gap-2">
      {#each subvolumes as sv}
        <button
          on:click={() => { selectedSubvolume = sv.path; refresh(); }}
          class="flex items-center gap-2 px-4 py-2 rounded-lg transition {selectedSubvolume === sv.path ? 'bg-blue-600' : 'bg-slate-800 hover:bg-slate-700'}"
        >
          <HardDrive class="w-4 h-4" />
          {sv.path.split("/").pop()}
        </button>
      {/each}
      {#if subvolumes.length === 0}
        <p class="text-slate-400">No btrfs volumes found</p>
      {/if}
    </div>
  </div>

  <!-- Create Dialog -->
  {#if showCreateDialog}
    <div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div class="bg-slate-800 rounded-xl p-6 w-full max-w-md">
        <h2 class="text-xl font-semibold mb-4">Create Snapshot</h2>
        <div class="mb-4">
          <label class="block text-sm text-slate-400 mb-1">Snapshot Name (optional)</label>
          <input
            type="text"
            bind:value={newSnapshotName}
            placeholder="Auto-generated if empty"
            class="w-full px-4 py-2 bg-slate-900 rounded-lg border border-slate-700 focus:border-blue-500 focus:outline-none"
          />
        </div>
        <p class="text-sm text-slate-400 mb-4">
          Creating snapshot of: <span class="text-white">{selectedSubvolume}</span>
        </p>
        <div class="flex justify-end gap-2">
          <button
            on:click={() => { showCreateDialog = false; newSnapshotName = ""; }}
            class="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg"
          >
            Cancel
          </button>
          <button
            on:click={handleCreate}
            disabled={creating}
            class="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg disabled:opacity-50"
          >
            {creating ? "Creating..." : "Create"}
          </button>
        </div>
      </div>
    </div>
  {/if}

  <!-- Snapshots List -->
  <div class="bg-slate-800 rounded-xl border border-slate-700 overflow-hidden">
    <div class="grid grid-cols-[1fr_150px_100px_150px] gap-4 px-4 py-3 bg-slate-900/50 text-sm text-slate-400 border-b border-slate-700">
      <div>Name</div>
      <div>Created</div>
      <div>Size</div>
      <div>Actions</div>
    </div>

    {#if loading}
      <div class="p-8 text-center text-slate-400">Loading...</div>
    {:else if snapshots.length === 0}
      <div class="p-8 text-center text-slate-400">
        <Database class="w-12 h-12 mx-auto mb-3 opacity-50" />
        <p>No snapshots yet</p>
        <p class="text-sm mt-1">Create a snapshot to backup your data</p>
      </div>
    {:else}
      {#each snapshots as snap}
        <div class="grid grid-cols-[1fr_150px_100px_150px] gap-4 px-4 py-3 border-b border-slate-700/50 hover:bg-slate-700/30 items-center">
          <div class="flex items-center gap-3">
            <Archive class="w-5 h-5 text-blue-400" />
            <div>
              <p class="font-medium">{snap.name}</p>
              <p class="text-xs text-slate-500 truncate">{snap.path}</p>
            </div>
          </div>
          <div class="text-sm text-slate-400">
            <div class="flex items-center gap-1">
              <Calendar class="w-4 h-4" />
              {formatDate(snap.created)}
            </div>
          </div>
          <div class="text-sm text-slate-400">
            {snap.size || "-"}
          </div>
          <div class="flex items-center gap-2">
            <button
              on:click={() => handleRestore(snap)}
              class="p-2 rounded-lg bg-slate-700 hover:bg-green-600 transition"
              title="Restore"
            >
              <RotateCcw class="w-4 h-4" />
            </button>
            <button
              on:click={() => handleDelete(snap)}
              class="p-2 rounded-lg bg-slate-700 hover:bg-red-600 transition"
              title="Delete"
            >
              <Trash2 class="w-4 h-4" />
            </button>
          </div>
        </div>
      {/each}
    {/if}
  </div>

  <!-- Info -->
  <div class="mt-6 p-4 bg-slate-800/50 rounded-lg border border-slate-700">
    <h3 class="font-medium mb-2">About Snapshots</h3>
    <p class="text-sm text-slate-400">
      Snapshots are instant, space-efficient backups using btrfs copy-on-write technology.
      They capture the exact state of your data at a point in time. Restoring creates a new
      directory with the snapshot's contents.
    </p>
  </div>
</div>
