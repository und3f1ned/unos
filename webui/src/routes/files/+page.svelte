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
  import { Alert, Button, Input, Modal } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
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
    Trash,
    Trash2,
    Upload,
    Download,
    RefreshCw,
    FolderOpen,
    Copy,
    FolderInput,
    ClipboardPaste,
    Pencil,
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
  // The path of the item whose name is a field in its row, and the field's text.
  let renaming: string | null = null;
  let renamingValue = "";
  // A rename is on its way, until the list shows the new name: neither Enter
  // nor leaving the field sends another.
  let renameSent = false;
  let error = "";
  // An upload, copy, move or delete is on its way.
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
      // A name field goes with its item: in another folder, or once the item is gone.
      if (renaming !== null && !result.items.some((item) => item.path === renaming)) renaming = null;
    } catch (e) {
      showError($t("files.error.open"), e);
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
      showError($t("files.error.download", { name: item.name }), e);
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
      showError($t("files.error.create"), e);
    }
  }

  // The Delete dialog (ADR-0259): the items it is about while it is open, which
  // of its choices is on its way, and the server's refusal. Delete for good is
  // on offer only once the server refused to move the items to the trash
  // because they have none there, or it cannot be used (409), or their pool
  // has no space left (507): Delete in Files is never one click away from
  // deleting for good.
  let deleting: string[] | null = null;
  let deleteSending: "trash" | "permanent" | null = null;
  let deleteError = "";
  let deleteErrorTitle = "";
  let offerForGood = false;
  // The toolbar's Delete button and the dialog's Cancel and Move to trash, for the keyboard focus.
  let deleteButtonSlot: HTMLSpanElement | null = null;
  let deleteCancelSlot: HTMLSpanElement | null = null;
  let moveToTrashSlot: HTMLSpanElement | null = null;

  // Move to trash takes the keyboard focus, as the browser's own confirmation
  // did: Enter moves the items to the trash, Escape keeps them.
  async function askDelete() {
    if (selected.size === 0) return;
    deleting = Array.from(selected);
    deleteError = "";
    offerForGood = false;
    await tick();
    moveToTrashSlot?.querySelector("button")?.focus();
  }

  async function closeDelete() {
    if (deleteSending) return;
    deleting = null;
    await tick();
    deleteButtonSlot?.querySelector("button")?.focus();
  }

  // Into the trash, or with `permanent` deleted for good at once. A refusal
  // keeps the dialog open with the server's reason, about the items that are
  // still in the folder: the server may have moved or deleted some before it
  // refused one, and says how many. The keyboard focus goes to Cancel.
  async function handleDelete(permanent: boolean) {
    if (!deleting || deleteSending || (permanent && !offerForGood)) return;
    error = "";
    deleteError = "";
    busy = true;
    deleteSending = permanent ? "permanent" : "trash";
    let refusal: unknown = null;
    try {
      await deleteItems(deleting, permanent);
      deleting = null;
      selected = new Set();
    } catch (e) {
      refusal = e;
    } finally {
      busy = false;
      deleteSending = null;
    }
    await refresh();
    if (refusal === null || deleting === null) return;
    if (refusal instanceof ApiError && refusal.status === 401) return;
    const title = permanent ? $t("files.error.forGood") : $t("files.error.toTrash");
    const message = refusal instanceof Error ? refusal.message : String(refusal);
    const listed = new Set(listing?.items.map((item) => item.path) ?? []);
    const left = deleting.filter((path) => listed.has(path));
    selected = new Set(left);
    if (left.length === 0) {
      deleting = null;
      error = `${title}: ${message}`;
      return;
    }
    deleting = left;
    deleteErrorTitle = title;
    deleteError = message;
    if (refusal instanceof ApiError && (refusal.status === 409 || refusal.status === 507)) offerForGood = true;
    await tick();
    deleteCancelSlot?.querySelector("button")?.focus();
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
      showError(mode === "copy" ? $t("files.error.copy") : $t("files.error.move"), e);
    } finally {
      busy = false;
    }
    await refresh();
  }

  function startRename(item: FileInfo) {
    renaming = item.path;
    renamingValue = item.name;
  }

  // Renames `item` to the name in its field: Enter and leaving the field call
  // it. Returns the item's path once its field is closed; null while the field
  // stays open because the server refused the name, and when there is nothing
  // to do: the blur of a field that is going away (after Escape or a rename),
  // or an Enter or a blur while the rename is on its way.
  async function handleRename(item: FileInfo): Promise<string | null> {
    if (renaming !== item.path || renameSent) return null;
    const name = renamingValue.trim();
    if (!name || name === item.name || renamingValue === item.name) {
      renaming = null;
      return item.path;
    }
    error = "";
    // Until the list shows the new name, the toolbar's actions and Copy here /
    // Move here wait (`working`): they would start with the old path.
    renameSent = true;
    try {
      let renamed: FileInfo;
      try {
        renamed = await renameItem(item.path, name);
      } catch (e) {
        showError($t("files.error.rename"), e);
        return null;
      }
      renaming = null;
      // The selection, and the items chosen for Copy or Move, follow the item
      // (and what is in it) to its new name.
      const follow = (path: string) =>
        path === item.path || path.startsWith(`${item.path}/`) ? renamed.path + path.slice(item.path.length) : path;
      selected = new Set(Array.from(selected, follow));
      if (pending) pending = { ...pending, paths: pending.paths.map(follow) };
      await refresh();
      return renamed.path;
    } finally {
      renameSent = false;
    }
  }

  // Enter renames, Escape keeps the name. Either way the keyboard focus goes on
  // at the item's checkbox, which a screen reader reads with the item's name.
  async function nameFieldKey(event: KeyboardEvent, item: FileInfo) {
    if (event.key === "Escape") {
      renaming = null;
      await focusCheckbox(item.path);
    } else if (event.key === "Enter") {
      const path = await handleRename(item);
      if (path !== null) await focusCheckbox(path);
    }
  }

  // Leaving the field renames the item. Shift+Tab leaves it for the item's
  // checkbox, whose row the rename replaces: the focus goes on at the new
  // row's checkbox. The field also gets a blur when the window or the tab
  // loses the focus (another app or tab, the address bar), while the page has
  // no focus: the field gets the focus back on the return, so it stays open,
  // as typed.
  async function nameFieldBlur(item: FileInfo) {
    if (!document.hasFocus()) return;
    const path = await handleRename(item);
    if (path !== null) await focusCheckbox(path);
  }

  async function focusCheckbox(path: string) {
    await tick();
    // Unless the focus has gone elsewhere meanwhile.
    if (document.activeElement !== null && document.activeElement !== document.body) return;
    const boxes = Array.from(listBox?.querySelectorAll<HTMLInputElement>("input[data-path]") ?? []);
    boxes.find((box) => box.dataset.path === path)?.focus();
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
    if (failed.length > 0) error = $t("files.error.upload", { files: failed.join(", ") });
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

  // While an upload, copy, move, delete or rename is on its way, the toolbar's
  // actions and Copy here / Move here wait: they would start with paths that
  // are changing.
  $: working = busy || renameSent;
  $: selectedItems = listing ? listing.items.filter((item) => selected.has(item.path)) : [];
  // Rename works on one item at a time, Download on one regular file.
  $: selectedItem = selectedItems.length === 1 ? selectedItems[0] : null;
  $: selectedFile = selectedItem?.type === "file" ? selectedItem : null;

  function getIcon(item: FileInfo) {
    if (item.type === "directory") return Folder;
    const ext = item.name.split(".").pop()?.toLowerCase() || "";
    if (["jpg", "jpeg", "png", "gif", "webp", "svg"].includes(ext)) return FileImage;
    if (["mp4", "mkv", "avi", "mov", "webm"].includes(ext)) return FileVideo;
    if (["mp3", "flac", "wav", "ogg", "m4a"].includes(ext)) return FileAudio;
    if (["txt", "md", "json", "xml", "yaml", "yml"].includes(ext)) return FileText;
    return File;
  }

  // From the pools directory, named `rootName`, down to `path`: nothing above it can be opened.
  function getBreadcrumbs(
    path: string | null,
    root: string | null,
    rootName: string,
  ): { name: string; path: string }[] {
    if (!path || !root) return [];
    const crumbs = [{ name: rootName, path: root }];
    const prefix = root.endsWith("/") ? root : `${root}/`;
    if (!path.startsWith(prefix)) return crumbs;
    let current = root;
    for (const part of path.slice(prefix.length).split("/").filter(Boolean)) {
      current = `${current.replace(/\/$/, "")}/${part}`;
      crumbs.push({ name: part, path: current });
    }
    return crumbs;
  }

  $: breadcrumbs = getBreadcrumbs(currentPath, rootPath, $t("files.pools"));

  // The columns of the list and its header: the check box, the name, the size and, from sm up,
  // the day of the last change. Fixed widths, so that the rows, each a grid, line up.
  const columnsClass = "grid-cols-[auto_minmax(0,1fr)_5rem] sm:grid-cols-[auto_1fr_100px_150px]";

  // Sizes in the units of the page's language.
  $: units = [$t("files.unit.b"), $t("files.unit.kb"), $t("files.unit.mb"), $t("files.unit.gb"), $t("files.unit.tb")];
</script>

<!-- On a narrow screen the page has no padding of its own beside main's, its buttons wrap, the
     list leaves out the Modified column, and the page grows with the list, so that main scrolls
     it as a whole. From md the list fills the rest of the window and scrolls inside it (ADR-0301). -->
<div class="md:p-6 md:h-full flex flex-col">
  <div class="flex flex-wrap items-center justify-between gap-2 mb-4">
    <h1 class="text-2xl font-bold flex items-center gap-2">
      <FolderOpen class="w-7 h-7" />
      {$t("files.title")}
    </h1>
    <div class="flex flex-wrap items-center gap-2">
      <input bind:this={uploadInput} type="file" multiple class="hidden" on:change={handleUpload} />
      <!-- The span shows the tooltip: Button does not pass a title on. -->
      <span class="flex" title={$t("files.upload")}>
        <Button variant="secondary" size="icon" on:click={() => uploadInput?.click()}>
          <Upload class="w-5 h-5" />
          <span class="sr-only">{$t("files.upload")}</span>
        </Button>
      </span>
      <button
        on:click={() => (showNewFolder = true)}
        class="p-2 rounded-lg bg-slate-700 hover:bg-slate-600 transition"
        title={$t("files.newFolder")}
      >
        <Plus class="w-5 h-5" />
      </button>
      <button
        on:click={refresh}
        class="p-2 rounded-lg bg-slate-700 hover:bg-slate-600 transition"
        title={$t("common.refresh")}
        disabled={loading}
      >
        <RefreshCw class="w-5 h-5 {loading ? 'animate-spin' : ''}" />
      </button>
      <Button variant="secondary" icon={Trash} href="/files/trash">{$t("files.trash.title")}</Button>
    </div>
  </div>

  {#if error}
    <!-- role="alert": screen readers announce the message when it appears. -->
    <div role="alert">
      <Alert variant="error" dismissible class="mb-4" on:dismiss={() => (error = "")}>{error}</Alert>
    </div>
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
  <div class="mb-4 flex flex-wrap items-center gap-2" role="toolbar" aria-label={$t("files.selectedItems")}>
    <span class="text-sm text-content-secondary mr-2">{$t("files.selected", { n: selected.size })}</span>
    <Button
      variant="secondary"
      size="sm"
      icon={Download}
      loading={downloading}
      disabled={working || !selectedFile}
      on:click={() => selectedFile && handleDownload(selectedFile)}
    >
      {$t("files.download")}
    </Button>
    <Button
      variant="secondary"
      size="sm"
      icon={Copy}
      disabled={working || selected.size === 0}
      on:click={() => startTransfer("copy")}
    >
      {$t("files.copy")}
    </Button>
    <Button
      variant="secondary"
      size="sm"
      icon={FolderInput}
      disabled={working || selected.size === 0}
      on:click={() => startTransfer("move")}
    >
      {$t("files.move")}
    </Button>
    <Button
      variant="secondary"
      size="sm"
      icon={Pencil}
      disabled={working || !selectedItem}
      on:click={() => selectedItem && startRename(selectedItem)}
    >
      {$t("files.rename")}
    </Button>
    <span bind:this={deleteButtonSlot} class="contents">
      <Button
        variant="danger"
        size="sm"
        icon={Trash2}
        disabled={working || selected.size === 0}
        on:click={askDelete}
      >
        {$t("files.delete")}
      </Button>
    </span>
  </div>

  <!-- Copy or move in progress: the destination is the folder that is open -->
  {#if pending}
    <div class="mb-4 flex flex-wrap items-center gap-2 card-nested py-2">
      <span class="text-sm text-content-secondary mr-2">
        {$t(pending.mode === "copy" ? "files.pending.copy" : "files.pending.move", { n: pending.paths.length })}
      </span>
      <Button
        variant="primary"
        size="sm"
        icon={ClipboardPaste}
        loading={busy}
        disabled={renameSent}
        on:click={finishTransfer}
      >
        {pending.mode === "copy" ? $t("files.copyHere") : $t("files.moveHere")}
      </Button>
      <Button variant="ghost" size="sm" disabled={working} on:click={() => (pending = null)}>{$t("common.cancel")}</Button>
    </div>
  {/if}

  <!-- New Folder: on a narrow screen the buttons wrap below the field (ADR-0301). -->
  {#if showNewFolder}
    <div class="mb-4 flex flex-wrap items-center gap-2">
      <Input
        bind:value={newFolderName}
        placeholder={$t("files.folderName")}
        class="flex-1 min-w-[10rem]"
        on:keydown={(e) => e.key === "Enter" && handleCreateFolder()}
      />
      <div class="flex gap-2">
        <Button on:click={handleCreateFolder}>{$t("files.create")}</Button>
        <Button variant="secondary" on:click={() => { showNewFolder = false; newFolderName = ""; }}>
          {$t("common.cancel")}
        </Button>
      </div>
    </div>
  {/if}

  <!-- File List -->
  <div class="md:flex-1 md:min-h-0 flex flex-col bg-surface-elevated rounded-xl border border-border overflow-hidden">
    <div class="grid {columnsClass} gap-4 px-4 py-2 bg-slate-900/50 text-sm text-slate-400 border-b border-slate-700">
      <div class="w-6"></div>
      <div>{$t("files.column.name")}</div>
      <div>{$t("files.column.size")}</div>
      <div class="hidden sm:block">{$t("files.column.modified")}</div>
    </div>

    <div
      bind:this={listBox}
      class="md:flex-1 md:min-h-0 overflow-y-auto"
      role="region"
      aria-label={$t("files.contents")}
    >
      {#if listing?.parent}
        <button
          bind:this={upButton}
          on:click={goUp}
          class="w-full grid {columnsClass} gap-4 px-4 py-3 hover:bg-slate-700/50 items-center text-left border-b border-slate-700/50"
          title={$t("files.up")}
        >
          <ArrowUp class="w-5 h-5 text-slate-400" />
          <span class="text-slate-400">..</span>
          <span></span>
          <span class="hidden sm:block"></span>
        </button>
      {/if}

      {#if listing?.items}
        {#each listing.items as item (item.path)}
          <!-- Mouse shortcuts: a click on the row selects the item, a double-click
               opens it. From the keyboard, the checkbox selects and the name opens. -->
          <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
          <div
            class="grid {columnsClass} gap-4 px-4 py-3 hover:bg-slate-700/50 items-center border-b border-slate-700/50 cursor-pointer {selected.has(item.path) ? 'bg-blue-600/20' : ''}"
            on:click={() => toggleSelect(item.path)}
            on:dblclick={() => handleOpen(item)}
          >
            <input
              type="checkbox"
              aria-label={$t("files.select", { name: item.name })}
              data-path={item.path}
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
                <!-- Clicks in the field, a double-click on a word too, stay out of the row. -->
                <Input
                  bind:value={renamingValue}
                  ariaLabel={$t("files.newName", { name: item.name })}
                  autocomplete="off"
                  spellcheck={false}
                  size="sm"
                  selectOnMount
                  class="min-w-0 flex-1"
                  on:keydown={(e) => nameFieldKey(e, item)}
                  on:blur={() => nameFieldBlur(item)}
                  on:click={(e) => e.stopPropagation()}
                  on:dblclick={(e) => e.stopPropagation()}
                />
              {:else if item.type === "directory" || item.type === "file"}
                <Button
                  variant="ghost"
                  size="sm"
                  class="pointer-events-none min-w-0 max-w-full justify-start -mx-1 px-1 py-0.5 text-base font-normal text-content hover:bg-transparent"
                  on:click={(e) => openFromName(e, item)}
                >
                  <span class="sr-only">{$t(item.type === "directory" ? "files.openItem" : "files.downloadItem", { name: item.name })}</span>
                  <span class="truncate" aria-hidden="true">{item.name}</span>
                </Button>
              {:else}
                <span class="truncate">{item.name}</span>
              {/if}
            </div>
            <span class="text-sm text-slate-400">
              {item.type === "directory" ? "-" : formatSize(item.size, units)}
            </span>
            <span class="hidden sm:block text-sm text-slate-400">
              {new Date(item.modified).toLocaleDateString($intlLocale)}
            </span>
          </div>
        {/each}
      {/if}
    </div>
  </div>

  <!-- Status Bar -->
  {#if listing}
    <div class="mt-2 text-sm text-slate-400 flex justify-between">
      <span>{$t("files.itemCount", { n: listing.item_count })}</span>
      <span>{formatSize(listing.total_size, units)}</span>
    </div>
  {/if}
</div>

<Modal
  open={deleting !== null}
  title={$t("files.delete.title", { n: deleting?.length ?? 0 })}
  closable={deleteSending === null}
  on:close={closeDelete}
>
  {#if deleting}
    <div class="space-y-3">
      {#if deleting.length === 1}
        <p class="text-sm">{$t("files.delete.one")}</p>
      {:else}
        <p class="text-sm">{$t("files.delete.many", { n: deleting.length })}</p>
      {/if}
      {#if deleteError}
        <!-- role="alert": screen readers announce the server's refusal. -->
        <div role="alert">
          <Alert variant="error" title={deleteErrorTitle}>{deleteError}</Alert>
        </div>
      {/if}
      {#if offerForGood}
        <p class="text-sm text-secondary">
          {#if deleting.length === 1}
            {$t("files.delete.forGoodOne")}
          {:else}
            {$t("files.delete.forGoodMany")}
          {/if}
        </p>
      {/if}
    </div>
  {/if}

  <svelte:fragment slot="footer">
    <span bind:this={deleteCancelSlot} class="contents">
      <Button variant="secondary" class="flex-1" disabled={deleteSending !== null} on:click={closeDelete}>
        {$t("common.cancel")}
      </Button>
    </span>
    {#if offerForGood}
      <Button
        variant="danger"
        class="flex-1"
        icon={Trash2}
        loading={deleteSending === "permanent"}
        disabled={deleteSending !== null}
        on:click={() => handleDelete(true)}
      >
        {$t("files.deleteForGood")}
      </Button>
    {/if}
    <span bind:this={moveToTrashSlot} class="contents">
      <Button
        class="flex-1"
        icon={Trash}
        loading={deleteSending === "trash"}
        disabled={deleteSending !== null}
        on:click={() => handleDelete(false)}
      >
        {$t("files.moveToTrash")}
      </Button>
    </span>
  </svelte:fragment>
</Modal>
