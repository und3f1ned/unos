<script lang="ts">
  import { onMount, tick } from "svelte";
  import { ApiError } from "$lib/api/client";
  import {
    listDirectory,
    createDirectory,
    deleteItems,
    renameItem,
    uploadFile,
    downloadFile,
    copyItems,
    moveItems,
    formatSize,
    type FileInfo,
    type DirectoryListing,
  } from "$lib/api/files";
  import { Alert, Button } from "$lib/components/ui";
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
    Copy,
    FolderInput,
    ClipboardPaste,
  } from "lucide-svelte";

  type Transfer = "copy" | "move";

  let listing: DirectoryListing | null = null;
  let loading = true;
  // The pools directory, the top of the file manager. The server decides
  // where it is (UNOS_POOLS_BASE_PATH): it lists it when asked for no path.
  let rootPath: string | null = null;
  // Directory shown; null until the first listing has arrived.
  let currentPath: string | null = null;
  let selected: Set<string> = new Set();
  let showNewFolder = false;
  let newFolderName = "";
  let renaming: string | null = null;
  let renamingValue = "";
  let error = "";
  let busy = false;
  let downloading = false;
  // Items chosen with Copy or Move; they go to the folder that is open when
  // "Copy here" / "Move here" is chosen.
  let pending: { mode: Transfer; paths: string[] } | null = null;

  onMount(() => load());

  async function load(path?: string) {
    loading = true;
    try {
      const result = await listDirectory(path);
      listing = result;
      currentPath = result.path;
      if (result.parent === null) rootPath = result.path;
    } catch (e) {
      showError("Could not open the folder", e);
    } finally {
      loading = false;
    }
  }

  function refresh() {
    return load(currentPath ?? undefined);
  }

  async function navigate(path: string) {
    selected = new Set();
    error = "";
    await load(path);
  }

  // A 401 has already ended the session and the sign-in page is on its way.
  function showError(action: string, e: unknown) {
    if (e instanceof ApiError && e.status === 401) return;
    error = `${action}: ${e instanceof Error ? e.message : String(e)}`;
  }

  async function handleOpen(item: FileInfo) {
    if (item.type === "directory") {
      await navigate(item.path);
    } else if (item.type === "file") {
      await handleDownload(item);
    }
  }

  // The ".." entry, the first one of the list of a folder.
  let upButton: HTMLButtonElement | null = null;

  // An item's name is a button for the keyboard and screen readers: Enter,
  // Space or a screen reader's click opens the item, like a double-click.
  // The mouse, pen or finger never reaches it (pointer-events-none): their
  // clicks go to the row, which selects the item, and a double-click opens it.
  // The event cannot tell a screen reader's click from a mouse click: Chrome
  // sends it with detail 1 and pointerType "mouse".
  async function openFromName(event: MouseEvent, item: FileInfo) {
    event.stopPropagation();
    await handleOpen(item);
    // The focused name went away with the old list: go on at the top of the new one.
    await tick();
    if (document.activeElement === null || document.activeElement === document.body) {
      upButton?.focus();
    }
  }

  // The list of the open folder: the ".." entry and the items.
  let listBox: HTMLDivElement | null = null;

  // Whether the keyboard focus is shown on `element`. Chrome also focuses a
  // button that is clicked with the mouse, but does not show it.
  function showsFocus(element: HTMLElement | null): boolean {
    if (element === null || element !== document.activeElement) return false;
    try {
      return element.matches(":focus-visible");
    } catch {
      return true; // a browser without :focus-visible
    }
  }

  async function goUp() {
    if (!listing?.parent) return;
    const fromKeyboard = showsFocus(upButton);
    await navigate(listing.parent);
    if (!fromKeyboard) return;
    // The pools directory has no ".." entry, so the focus went with it: go on
    // at the first item's name, or at the list when there is none.
    await tick();
    if (document.activeElement !== null && document.activeElement !== document.body) return;
    const firstName = listBox?.querySelector<HTMLElement>("button");
    if (firstName) {
      firstName.focus();
    } else if (listBox) {
      // Focusable only until it loses the focus: a list with a tabindex would
      // take the focus of every mouse click on a row. Leaving the window also
      // sends a blur, but the list keeps the focus for the return.
      const list = listBox;
      const release = () => {
        if (document.activeElement === list) return;
        list.removeAttribute("tabindex");
        list.removeEventListener("blur", release);
      };
      list.tabIndex = -1;
      list.addEventListener("blur", release);
      list.focus();
    }
  }

  async function handleDownload(item: FileInfo) {
    error = "";
    downloading = true;
    try {
      await downloadFile(item.path);
    } catch (e) {
      showError(`Could not download ${item.name}`, e);
    } finally {
      downloading = false;
    }
  }

  async function handleCreateFolder() {
    if (!newFolderName.trim() || !currentPath) return;
    error = "";
    try {
      await createDirectory(currentPath, newFolderName.trim());
      newFolderName = "";
      showNewFolder = false;
      await refresh();
    } catch (e) {
      showError("Could not create the folder", e);
    }
  }

  async function handleDelete() {
    if (selected.size === 0) return;
    if (!confirm(`Delete ${selected.size} item(s)?`)) return;
    error = "";
    busy = true;
    try {
      await deleteItems(Array.from(selected));
      selected = new Set();
    } catch (e) {
      showError("Could not delete", e);
    } finally {
      busy = false;
    }
    await refresh();
  }

  function startTransfer(mode: Transfer) {
    pending = { mode, paths: Array.from(selected) };
    selected = new Set();
  }

  async function finishTransfer() {
    if (!pending || !currentPath) return;
    const { mode, paths } = pending;
    error = "";
    busy = true;
    try {
      if (mode === "copy") {
        await copyItems(paths, currentPath);
      } else {
        await moveItems(paths, currentPath);
      }
      pending = null;
    } catch (e) {
      showError(mode === "copy" ? "Could not copy" : "Could not move", e);
    } finally {
      busy = false;
    }
    await refresh();
  }

  async function handleRename(item: FileInfo) {
    if (!renamingValue.trim() || renamingValue === item.name) {
      renaming = null;
      return;
    }
    error = "";
    try {
      await renameItem(item.path, renamingValue.trim());
      renaming = null;
      await refresh();
    } catch (e) {
      showError("Could not rename", e);
    }
  }

  // The file chooser of Upload. It is hidden: the Upload button opens it, also
  // from the keyboard.
  let uploadInput: HTMLInputElement | null = null;

  async function handleUpload(e: Event) {
    const input = e.target as HTMLInputElement;
    const files = Array.from(input.files ?? []);
    input.value = "";
    // Every file goes to the folder that is open now, also if another folder
    // is opened while the files are uploading.
    const destination = currentPath;
    if (files.length === 0 || !destination) return;

    error = "";
    busy = true;
    const failed: string[] = [];
    try {
      for (const file of files) {
        try {
          await uploadFile(destination, file);
        } catch (err) {
          if (err instanceof ApiError && err.status === 401) return;
          failed.push(`${file.name} (${err instanceof Error ? err.message : String(err)})`);
        }
      }
    } finally {
      busy = false;
    }
    if (failed.length > 0) error = `Could not upload ${failed.join(", ")}`;
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

  $: selectedItems = listing ? listing.items.filter((item) => selected.has(item.path)) : [];
  // Download works on one regular file at a time.
  $: selectedFile =
    selectedItems.length === 1 && selectedItems[0].type === "file" ? selectedItems[0] : null;

  function getIcon(item: FileInfo) {
    if (item.type === "directory") return Folder;
    const ext = item.name.split(".").pop()?.toLowerCase() || "";
    if (["jpg", "jpeg", "png", "gif", "webp", "svg"].includes(ext)) return FileImage;
    if (["mp4", "mkv", "avi", "mov", "webm"].includes(ext)) return FileVideo;
    if (["mp3", "flac", "wav", "ogg", "m4a"].includes(ext)) return FileAudio;
    if (["txt", "md", "json", "xml", "yaml", "yml"].includes(ext)) return FileText;
    return File;
  }

  // From the pools directory down to `path`: nothing above it can be opened.
  function getBreadcrumbs(path: string | null, root: string | null): { name: string; path: string }[] {
    if (!path || !root) return [];
    const crumbs = [{ name: "Pools", path: root }];
    const prefix = root.endsWith("/") ? root : `${root}/`;
    if (!path.startsWith(prefix)) return crumbs;
    let current = root;
    for (const part of path.slice(prefix.length).split("/").filter(Boolean)) {
      current = `${current.replace(/\/$/, "")}/${part}`;
      crumbs.push({ name: part, path: current });
    }
    return crumbs;
  }

  $: breadcrumbs = getBreadcrumbs(currentPath, rootPath);
</script>

<div class="p-6 h-full flex flex-col">
  <div class="flex items-center justify-between mb-4">
    <h1 class="text-2xl font-bold flex items-center gap-2">
      <FolderOpen class="w-7 h-7" />
      File Browser
    </h1>
    <div class="flex items-center gap-2">
      <input bind:this={uploadInput} type="file" multiple class="hidden" on:change={handleUpload} />
      <!-- The span shows the tooltip: Button does not pass a title on. -->
      <span class="flex" title="Upload">
        <Button variant="secondary" size="icon" on:click={() => uploadInput?.click()}>
          <Upload class="w-5 h-5" />
          <span class="sr-only">Upload</span>
        </Button>
      </span>
      <button
        on:click={() => (showNewFolder = true)}
        class="p-2 rounded-lg bg-slate-700 hover:bg-slate-600 transition"
        title="New Folder"
      >
        <Plus class="w-5 h-5" />
      </button>
      <button
        on:click={refresh}
        class="p-2 rounded-lg bg-slate-700 hover:bg-slate-600 transition"
        title="Refresh"
        disabled={loading}
      >
        <RefreshCw class="w-5 h-5 {loading ? 'animate-spin' : ''}" />
      </button>
    </div>
  </div>

  {#if error}
    <Alert variant="error" dismissible class="mb-4" on:dismiss={() => (error = "")}>{error}</Alert>
  {/if}

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

  <!-- Actions on the selected items. Always shown: a bar that appeared on the
       first click of a double-click would move the rows under the pointer. -->
  <div class="mb-4 flex flex-wrap items-center gap-2" role="toolbar" aria-label="Selected items">
    <span class="text-sm text-content-secondary mr-2">{selected.size} selected</span>
    <Button
      variant="secondary"
      size="sm"
      icon={Download}
      loading={downloading}
      disabled={!selectedFile}
      on:click={() => selectedFile && handleDownload(selectedFile)}
    >
      Download
    </Button>
    <Button
      variant="secondary"
      size="sm"
      icon={Copy}
      disabled={busy || selected.size === 0}
      on:click={() => startTransfer("copy")}
    >
      Copy
    </Button>
    <Button
      variant="secondary"
      size="sm"
      icon={FolderInput}
      disabled={busy || selected.size === 0}
      on:click={() => startTransfer("move")}
    >
      Move
    </Button>
    <Button
      variant="danger"
      size="sm"
      icon={Trash2}
      disabled={busy || selected.size === 0}
      on:click={handleDelete}
    >
      Delete
    </Button>
  </div>

  <!-- Copy or move in progress: the destination is the folder that is open -->
  {#if pending}
    <div class="mb-4 flex flex-wrap items-center gap-2 card-nested py-2">
      <span class="text-sm text-content-secondary mr-2">
        {pending.paths.length} item(s) to {pending.mode}. Open the destination folder, then choose
        {pending.mode === "copy" ? "Copy here" : "Move here"}.
      </span>
      <Button variant="primary" size="sm" icon={ClipboardPaste} loading={busy} on:click={finishTransfer}>
        {pending.mode === "copy" ? "Copy here" : "Move here"}
      </Button>
      <Button variant="ghost" size="sm" disabled={busy} on:click={() => (pending = null)}>Cancel</Button>
    </div>
  {/if}

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

    <div
      bind:this={listBox}
      class="overflow-y-auto"
      style="max-height: calc(100vh - 280px)"
      role="region"
      aria-label="Folder contents"
    >
      {#if listing?.parent}
        <button
          bind:this={upButton}
          on:click={goUp}
          class="w-full grid grid-cols-[auto_1fr_100px_150px] gap-4 px-4 py-3 hover:bg-slate-700/50 items-center text-left border-b border-slate-700/50"
          title="Up one folder"
        >
          <ArrowUp class="w-5 h-5 text-slate-400" />
          <span class="text-slate-400">..</span>
          <span></span>
          <span></span>
        </button>
      {/if}

      {#if listing?.items}
        {#each listing.items as item (item.path)}
          <!-- Mouse shortcuts: a click on the row selects the item, a double-click
               opens it. From the keyboard, the checkbox selects and the name opens. -->
          <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
          <div
            class="grid grid-cols-[auto_1fr_100px_150px] gap-4 px-4 py-3 hover:bg-slate-700/50 items-center border-b border-slate-700/50 cursor-pointer {selected.has(item.path) ? 'bg-blue-600/20' : ''}"
            on:click={() => toggleSelect(item.path)}
            on:dblclick={() => handleOpen(item)}
          >
            <input
              type="checkbox"
              aria-label="Select {item.name}"
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
              {:else if item.type === "directory" || item.type === "file"}
                <Button
                  variant="ghost"
                  size="sm"
                  class="pointer-events-none min-w-0 max-w-full justify-start -mx-1 px-1 py-0.5 text-base font-normal text-content hover:bg-transparent"
                  on:click={(e) => openFromName(e, item)}
                >
                  <span class="sr-only">{item.type === "directory" ? "Open" : "Download"} {item.name}</span>
                  <span class="truncate" aria-hidden="true">{item.name}</span>
                </Button>
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
