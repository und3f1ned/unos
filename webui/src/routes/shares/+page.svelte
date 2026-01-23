<script lang="ts">
  import { onMount } from "svelte";
  import {
    getSmbShares,
    getNfsExports,
    getSharePaths,
    createSmbShare,
    updateSmbShare,
    deleteSmbShare,
    createNfsExport,
    deleteNfsExport,
    type SmbShare,
    type NfsExport,
    type SharePath,
  } from "$lib/api/shares";
  import { FolderOpen, Plus, Trash2, Edit, X } from "lucide-svelte";

  let smbShares: SmbShare[] = [];
  let nfsExports: NfsExport[] = [];
  let availablePaths: SharePath[] = [];
  let loading = true;
  let error = "";
  let activeTab: "smb" | "nfs" = "smb";

  // Modal state
  let showModal = false;
  let modalType: "smb" | "nfs" = "smb";
  let modalMode: "create" | "edit" = "create";
  let editingShareName = "";
  let saving = false;

  // SMB form
  let smbForm = {
    name: "",
    path: "",
    comment: "",
    guest_ok: false,
    readonly: false,
  };

  // NFS form
  let nfsForm = { path: "", clients: "*", options: "rw,sync,no_subtree_check" };

  onMount(async () => {
    await loadData();
  });

  async function loadData() {
    loading = true;
    try {
      [smbShares, nfsExports, availablePaths] = await Promise.all([
        getSmbShares(),
        getNfsExports(),
        getSharePaths(),
      ]);
    } catch (e: any) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  function openCreateModal() {
    modalType = activeTab;
    modalMode = "create";
    editingShareName = "";
    smbForm = {
      name: "",
      path: availablePaths.length > 0 ? availablePaths[0].path : "",
      comment: "",
      guest_ok: false,
      readonly: false,
    };
    nfsForm = {
      path: availablePaths.length > 0 ? availablePaths[0].path : "",
      clients: "*",
      options: "rw,sync,no_subtree_check",
    };
    showModal = true;
  }

  function openEditModal(share: SmbShare) {
    modalType = "smb";
    modalMode = "edit";
    editingShareName = share.name;
    smbForm = {
      name: share.name,
      path: share.path,
      comment: share.comment || "",
      guest_ok: share.guest_ok,
      readonly: share.readonly,
    };
    showModal = true;
  }

  async function handleSubmit() {
    saving = true;
    error = "";
    try {
      if (modalType === "smb") {
        if (modalMode === "edit") {
          await updateSmbShare(editingShareName, smbForm);
        } else {
          await createSmbShare(smbForm);
        }
      } else {
        await createNfsExport(nfsForm);
      }
      showModal = false;
      await loadData();
    } catch (e: any) {
      error = e.message;
    } finally {
      saving = false;
    }
  }

  async function handleDeleteSmb(name: string) {
    if (!confirm(`Delete share "${name}"?`)) return;
    try {
      await deleteSmbShare(name);
      await loadData();
    } catch (e: any) {
      error = e.message;
    }
  }

  async function handleDeleteNfs(path: string) {
    if (!confirm(`Delete export "${path}"?`)) return;
    try {
      await deleteNfsExport(path);
      await loadData();
    } catch (e: any) {
      error = e.message;
    }
  }
</script>

<div class="space-y-6">
  <div class="flex items-center justify-between">
    <h1 class="text-2xl font-bold">Shares</h1>
    <button
      on:click={openCreateModal}
      disabled={availablePaths.length === 0}
      class="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-slate-600 disabled:cursor-not-allowed rounded-lg transition-colors"
    >
      <Plus class="w-4 h-4" />
      Add {activeTab === "smb" ? "SMB Share" : "NFS Export"}
    </button>
  </div>

  {#if availablePaths.length === 0 && !loading}
    <div class="bg-yellow-500/20 border border-yellow-500 text-yellow-400 px-4 py-3 rounded">
      No storage pools available. Create a storage pool first before adding shares.
    </div>
  {/if}

  <!-- Tabs -->
  <div class="flex gap-2 border-b border-slate-700">
    <button
      class="px-4 py-2 -mb-px transition-colors {activeTab === 'smb' ? 'border-b-2 border-blue-500 text-blue-500' : 'text-slate-400 hover:text-white'}"
      on:click={() => (activeTab = "smb")}
    >
      SMB Shares
    </button>
    <button
      class="px-4 py-2 -mb-px transition-colors {activeTab === 'nfs' ? 'border-b-2 border-blue-500 text-blue-500' : 'text-slate-400 hover:text-white'}"
      on:click={() => (activeTab = "nfs")}
    >
      NFS Exports
    </button>
  </div>

  {#if error}
    <div class="bg-red-500/20 border border-red-500 text-red-400 px-4 py-3 rounded">
      {error}
    </div>
  {/if}

  {#if loading}
    <div class="text-center text-slate-500 py-8">Loading...</div>
  {:else if activeTab === "smb"}
    <div class="bg-slate-800 rounded-lg p-4">
      {#if smbShares.length === 0}
        <p class="text-slate-500 text-center py-4">No SMB shares configured</p>
      {:else}
        <div class="space-y-3">
          {#each smbShares as share}
            <div class="bg-slate-700/50 rounded-lg p-4 flex items-center justify-between">
              <div class="flex items-center gap-4">
                <FolderOpen class="w-8 h-8 text-yellow-500" />
                <div>
                  <h3 class="font-semibold">{share.name}</h3>
                  <p class="text-sm text-slate-400 font-mono">{share.path}</p>
                  {#if share.comment}
                    <p class="text-sm text-slate-500">{share.comment}</p>
                  {/if}
                </div>
              </div>
              <div class="flex items-center gap-4">
                <div class="flex gap-2 text-xs">
                  {#if share.readonly}
                    <span class="px-2 py-1 bg-yellow-500/20 text-yellow-400 rounded">Read Only</span>
                  {/if}
                  {#if share.guest_ok}
                    <span class="px-2 py-1 bg-green-500/20 text-green-400 rounded">Guest OK</span>
                  {/if}
                </div>
                <button
                  on:click={() => openEditModal(share)}
                  class="p-2 text-slate-400 hover:text-white transition-colors"
                  title="Edit share"
                >
                  <Edit class="w-4 h-4" />
                </button>
                <button
                  on:click={() => handleDeleteSmb(share.name)}
                  class="p-2 text-red-400 hover:text-red-300 transition-colors"
                  title="Delete share"
                >
                  <Trash2 class="w-4 h-4" />
                </button>
              </div>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  {:else}
    <div class="bg-slate-800 rounded-lg p-4">
      {#if nfsExports.length === 0}
        <p class="text-slate-500 text-center py-4">No NFS exports configured</p>
      {:else}
        <div class="space-y-3">
          {#each nfsExports as exp}
            <div class="bg-slate-700/50 rounded-lg p-4 flex items-center justify-between">
              <div>
                <p class="font-mono">{exp.path}</p>
                <p class="text-sm text-slate-400">{exp.clients} ({exp.options})</p>
              </div>
              <button
                on:click={() => handleDeleteNfs(exp.path)}
                class="p-2 text-red-400 hover:text-red-300 transition-colors"
              >
                <Trash2 class="w-4 h-4" />
              </button>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  {/if}
</div>

<!-- Modal -->
{#if showModal}
  <div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
    <div class="bg-slate-800 rounded-lg p-6 w-full max-w-md">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-xl font-bold">
          {#if modalMode === "edit"}
            Edit SMB Share
          {:else}
            {modalType === "smb" ? "Add SMB Share" : "Add NFS Export"}
          {/if}
        </h2>
        <button
          on:click={() => (showModal = false)}
          class="text-slate-400 hover:text-white"
        >
          <X class="w-5 h-5" />
        </button>
      </div>

      <form on:submit|preventDefault={handleSubmit} class="space-y-4">
        {#if modalType === "smb"}
          <div>
            <label for="smb-name" class="block text-sm text-slate-400 mb-1">Share Name</label>
            <input
              id="smb-name"
              type="text"
              bind:value={smbForm.name}
              class="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:border-blue-500 focus:outline-none"
              placeholder="myshare"
              required
              disabled={modalMode === "edit"}
            />
          </div>
          <div>
            <label for="smb-path" class="block text-sm text-slate-400 mb-1">Path</label>
            <select
              id="smb-path"
              bind:value={smbForm.path}
              class="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:border-blue-500 focus:outline-none"
              required
            >
              {#each availablePaths as p}
                <option value={p.path}>{p.label}</option>
              {/each}
            </select>
          </div>
          <div>
            <label for="smb-comment" class="block text-sm text-slate-400 mb-1">Comment</label>
            <input
              id="smb-comment"
              type="text"
              bind:value={smbForm.comment}
              class="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:border-blue-500 focus:outline-none"
              placeholder="Optional description"
            />
          </div>
          <div class="flex gap-4">
            <label class="flex items-center gap-2">
              <input
                type="checkbox"
                bind:checked={smbForm.guest_ok}
                class="rounded bg-slate-700 border-slate-600"
              />
              <span class="text-sm">Allow Guest</span>
            </label>
            <label class="flex items-center gap-2">
              <input
                type="checkbox"
                bind:checked={smbForm.readonly}
                class="rounded bg-slate-700 border-slate-600"
              />
              <span class="text-sm">Read Only</span>
            </label>
          </div>
        {:else}
          <div>
            <label for="nfs-path" class="block text-sm text-slate-400 mb-1">Path</label>
            <select
              id="nfs-path"
              bind:value={nfsForm.path}
              class="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:border-blue-500 focus:outline-none"
              required
            >
              {#each availablePaths as p}
                <option value={p.path}>{p.label}</option>
              {/each}
            </select>
          </div>
          <div>
            <label for="nfs-clients" class="block text-sm text-slate-400 mb-1">Allowed Clients</label>
            <input
              id="nfs-clients"
              type="text"
              bind:value={nfsForm.clients}
              class="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:border-blue-500 focus:outline-none"
              placeholder="* or 192.168.1.0/24"
            />
          </div>
          <div>
            <label for="nfs-options" class="block text-sm text-slate-400 mb-1">Options</label>
            <input
              id="nfs-options"
              type="text"
              bind:value={nfsForm.options}
              class="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:border-blue-500 focus:outline-none"
              placeholder="rw,sync,no_subtree_check"
            />
          </div>
        {/if}

        <div class="flex gap-3 pt-2">
          <button
            type="button"
            on:click={() => (showModal = false)}
            class="flex-1 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={saving}
            class="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 rounded-lg"
          >
            {saving ? "Saving..." : (modalMode === "edit" ? "Save" : "Create")}
          </button>
        </div>
      </form>
    </div>
  </div>
{/if}
