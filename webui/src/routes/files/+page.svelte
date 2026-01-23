<script lang="ts">
  import { onMount } from "svelte";
  import {
    listDirectory,
    createDirectory,
    deleteItems,
    renameItem,
    uploadFile,
    formatSize,
    type FileInfo,
    type DirectoryListing,
  } from "$lib/api/files";
  import {
    Folder,
    File,
    FileText,
    FileImage,
    FileVideo,
    FileAudio,
    ChevronRight,
    ArrowUp,
    Plus,
    Trash2,
    Upload,
    Download,
    RefreshCw,
    FolderOpen,
  } from "lucide-svelte";

  let listing: DirectoryListing | null = null;
  let loading = true;
  let currentPath = "/mnt/pools";
  let selected: Set<string> = new Set();
  let showNewFolder = false;
  let newFolderName = "";
  let renaming: string | null = null;
  let renamingValue = "";

  onMount(() => refresh());

  async function refresh() {
    loading = true;
    try {
      listing = await listDirectory(currentPath);
    } catch (e) {
      console.error(e);
    }
    loading = false;
  }

  async function navigate(path: string) {
    currentPath = path;
    selected.clear();
    await refresh();
  }

  async function handleOpen(item: FileInfo) {
    if (item.type === "directory") {
      await navigate(item.path);
    } else {
      window.open(`/api/v1/files/download?path=${encodeURIComponent(item.path)}`, "_blank");
    }
  }

  async function handleCreateFolder() {
    if (!newFolderName.trim()) return;
    try {
      await createDirectory(currentPath, newFolderName.trim());
      newFolderName = "";
      showNewFolder = false;
      await refresh();
    } catch (e: any) {
      alert(e.message);
    }
  }

  async function handleDelete() {
    if (selected.size === 0) return;
    if (!confirm(`Delete ${selected.size} item(s)?`)) return;
    try {
      await deleteItems(Array.from(selected));
      selected.clear();
      await refresh();
    } catch (e: any) {
      alert(e.message);
    }
  }

  async function handleRename(item: FileInfo) {
    if (!renamingValue.trim() || renamingValue === item.name) {
      renaming = null;
      return;
    }
    try {
      await renameItem(item.path, renamingValue.trim());
      renaming = null;
      await refresh();
    } catch (e: any) {
      alert(e.message);
    }
  }

  async function handleUpload(e: Event) {
    const input = e.target as HTMLInputElement;
    if (!input.files?.length) return;

    for (const file of input.files) {
      try {
        await uploadFile(currentPath, file);
      } catch (e: any) {
        alert(`Failed to upload ${file.name}: ${e.message}`);
      }
    }
    input.value = "";
    await refresh();
  }

  function toggleSelect(path: string) {
    if (selected.has(path)) {
      selected.delete(path);
    } else {
      selected.add(path);
    }
    selected = selected;
  }

  function getIcon(item: FileInfo) {
    if (item.type === "directory") return Folder;
    const ext = item.name.split(".").pop()?.toLowerCase() || "";
    if (["jpg", "jpeg", "png", "gif", "webp", "svg"].includes(ext)) return FileImage;
    if (["mp4", "mkv", "avi", "mov", "webm"].includes(ext)) return FileVideo;
    if (["mp3", "flac", "wav", "ogg", "m4a"].includes(ext)) return FileAudio;
    if (["txt", "md", "json", "xml", "yaml", "yml"].includes(ext)) return FileText;
    return File;
  }

  function getBreadcrumbs(path: string): { name: string; path: string }[] {
    const parts = path.split("/").filter(Boolean);
    const crumbs = [{ name: "Root", path: "/" }];
    let current = "";
    for (const part of parts) {
      current += "/" + part;
      crumbs.push({ name: part, path: current });
    }
    return crumbs;
  }

  $: breadcrumbs = getBreadcrumbs(currentPath);
</script>

<div class="p-6 h-full flex flex-col">
  <div class="flex items-center justify-between mb-4">
    <h1 class="text-2xl font-bold flex items-center gap-2">
      <FolderOpen class="w-7 h-7" />
      File Browser
    </h1>
    <div class="flex items-center gap-2">
      <label class="p-2 rounded-lg bg-slate-700 hover:bg-slate-600 cursor-pointer transition">
        <Upload class="w-5 h-5" />
        <input type="file" multiple class="hidden" on:change={handleUpload} />
      </label>
      <button
        on:click={() => (showNewFolder = true)}
        class="p-2 rounded-lg bg-slate-700 hover:bg-slate-600 transition"
        title="New Folder"
      >
        <Plus class="w-5 h-5" />
      </button>
      {#if selected.size > 0}
        <button
          on:click={handleDelete}
          class="p-2 rounded-lg bg-red-600 hover:bg-red-500 transition"
          title="Delete"
        >
          <Trash2 class="w-5 h-5" />
        </button>
      {/if}
      <button
        on:click={refresh}
        class="p-2 rounded-lg bg-slate-700 hover:bg-slate-600 transition"
        disabled={loading}
      >
        <RefreshCw class="w-5 h-5 {loading ? 'animate-spin' : ''}" />
      </button>
    </div>
  </div>

  <!-- Breadcrumbs -->
  <div class="flex items-center gap-1 mb-4 text-sm overflow-x-auto">
    {#each breadcrumbs as crumb, i}
      {#if i > 0}
        <ChevronRight class="w-4 h-4 text-slate-500 flex-shrink-0" />
      {/if}
      <button
        on:click={() => navigate(crumb.path)}
        class="px-2 py-1 rounded hover:bg-slate-700 truncate {i === breadcrumbs.length - 1 ? 'text-blue-400' : 'text-slate-300'}"
      >
        {crumb.name}
      </button>
    {/each}
  </div>

  <!-- New Folder Dialog -->
  {#if showNewFolder}
    <div class="mb-4 flex items-center gap-2">
      <input
        type="text"
        bind:value={newFolderName}
        placeholder="Folder name"
        class="flex-1 px-3 py-2 bg-slate-800 rounded-lg border border-slate-700 focus:border-blue-500 focus:outline-none"
        on:keydown={(e) => e.key === "Enter" && handleCreateFolder()}
      />
      <button
        on:click={handleCreateFolder}
        class="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg"
      >
        Create
      </button>
      <button
        on:click={() => { showNewFolder = false; newFolderName = ""; }}
        class="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg"
      >
        Cancel
      </button>
    </div>
  {/if}

  <!-- File List -->
  <div class="flex-1 bg-slate-800 rounded-xl border border-slate-700 overflow-hidden">
    <div class="grid grid-cols-[auto_1fr_100px_150px] gap-4 px-4 py-2 bg-slate-900/50 text-sm text-slate-400 border-b border-slate-700">
      <div class="w-6"></div>
      <div>Name</div>
      <div>Size</div>
      <div>Modified</div>
    </div>

    <div class="overflow-y-auto" style="max-height: calc(100vh - 280px)">
      {#if listing?.parent}
        <button
          on:click={() => navigate(listing.parent)}
          class="w-full grid grid-cols-[auto_1fr_100px_150px] gap-4 px-4 py-3 hover:bg-slate-700/50 items-center text-left border-b border-slate-700/50"
        >
          <ArrowUp class="w-5 h-5 text-slate-400" />
          <span class="text-slate-400">..</span>
          <span></span>
          <span></span>
        </button>
      {/if}

      {#if listing?.items}
        {#each listing.items as item}
          <div
            class="grid grid-cols-[auto_1fr_100px_150px] gap-4 px-4 py-3 hover:bg-slate-700/50 items-center border-b border-slate-700/50 cursor-pointer {selected.has(item.path) ? 'bg-blue-600/20' : ''}"
            on:click={() => toggleSelect(item.path)}
            on:dblclick={() => handleOpen(item)}
          >
            <input
              type="checkbox"
              checked={selected.has(item.path)}
              on:click|stopPropagation={() => toggleSelect(item.path)}
              class="w-4 h-4 rounded bg-slate-700 border-slate-600"
            />
            <div class="flex items-center gap-3 min-w-0">
              <svelte:component
                this={getIcon(item)}
                class="w-5 h-5 flex-shrink-0 {item.type === 'directory' ? 'text-yellow-400' : 'text-slate-400'}"
              />
              {#if renaming === item.path}
                <input
                  type="text"
                  bind:value={renamingValue}
                  class="flex-1 px-2 py-1 bg-slate-900 rounded border border-blue-500 focus:outline-none"
                  on:keydown={(e) => {
                    if (e.key === "Enter") handleRename(item);
                    if (e.key === "Escape") renaming = null;
                  }}
                  on:blur={() => handleRename(item)}
                  on:click|stopPropagation
                />
              {:else}
                <span class="truncate">{item.name}</span>
              {/if}
            </div>
            <span class="text-sm text-slate-400">
              {item.type === "directory" ? "-" : formatSize(item.size)}
            </span>
            <span class="text-sm text-slate-400">
              {new Date(item.modified).toLocaleDateString()}
            </span>
          </div>
        {/each}
      {/if}
    </div>
  </div>

  <!-- Status Bar -->
  {#if listing}
    <div class="mt-2 text-sm text-slate-400 flex justify-between">
      <span>{listing.item_count} items</span>
      <span>{formatSize(listing.total_size)}</span>
    </div>
  {/if}
</div>
