<script lang="ts">
  // The trash of every pool and volume (ADR-0259): what Delete in Files moved
  // there, until it is restored, deleted for good, or deleted for good by the
  // daily expiry.
  import { onMount, tick } from "svelte";
  import { ApiError } from "$lib/api/client";
  import {
    deleteFromTrash,
    emptyTrash,
    formatSize,
    listTrash,
    restoreFromTrash,
    type TrashItem,
  } from "$lib/api/files";
  import { Alert, Button, Card, EmptyState, Modal } from "$lib/components/ui";
  import { ArrowLeft, File, Folder, Link, RefreshCw, RotateCcw, Trash, Trash2 } from "lucide-svelte";

  let items: TrashItem[] = [];
  let retentionDays: number | null = null;
  // The trashes that could not be read, each with the reason.
  let problems: string[] = [];
  let loading = true;
  let loadError = "";
  // The refusal of a restore, and what the last action did.
  let error = "";
  let notice = "";
  let selected: Set<string> = new Set();
  // A restore, a deletion for good or the emptying is on its way.
  let busy = false;
  let restoring = false;
  // The confirmation of a deletion for good: of the selected items, or of everything.
  let confirming: "selected" | "everything" | null = null;
  let dialogError = "";
  // Each dialog's Cancel, which takes the keyboard focus: what the dialogs do cannot be undone.
  let cancelSlot: HTMLSpanElement | null = null;

  onMount(load);

  function messageOf(e: unknown): string {
    return e instanceof Error ? e.message : String(e);
  }

  // A 401 has already ended the session and the sign-in page is on its way.
  function signedOut(e: unknown): boolean {
    return e instanceof ApiError && e.status === 401;
  }

  async function load() {
    loading = true;
    loadError = "";
    try {
      const listing = await listTrash();
      items = listing.items;
      retentionDays = listing.retention_days;
      problems = listing.problems;
      // What is no longer listed is no longer selected.
      const listed = new Set(items.map((item) => item.path));
      selected = new Set(Array.from(selected).filter((path) => listed.has(path)));
    } catch (e) {
      if (!signedOut(e)) loadError = messageOf(e);
    } finally {
      loading = false;
    }
  }

  function toggle(path: string) {
    if (selected.has(path)) {
      selected.delete(path);
    } else {
      selected.add(path);
    }
    selected = selected;
  }

  function plural(count: number, one: string, many: string): string {
    return count === 1 ? one : many;
  }

  async function handleRestore() {
    if (selected.size === 0 || busy) return;
    error = "";
    notice = "";
    busy = true;
    restoring = true;
    try {
      const { restored } = await restoreFromTrash(Array.from(selected));
      selected = new Set();
      notice =
        restored.length === 1
          ? `Restored to ${restored[0]}.`
          : `Restored ${restored.length} items to where they were deleted from.`;
    } catch (e) {
      if (!signedOut(e)) error = `Could not restore: ${messageOf(e)}`;
    } finally {
      busy = false;
      restoring = false;
    }
    await load();
  }

  async function ask(what: "selected" | "everything") {
    confirming = what;
    dialogError = "";
    await tick();
    cancelSlot?.querySelector("button")?.focus();
  }

  function closeDialog() {
    if (busy) return;
    confirming = null;
  }

  async function handleDeleteForGood() {
    if (!confirming || busy) return;
    error = "";
    notice = "";
    dialogError = "";
    busy = true;
    try {
      const count =
        confirming === "everything"
          ? (await emptyTrash()).deleted
          : (await deleteFromTrash(Array.from(selected))).deleted.length;
      confirming = null;
      selected = new Set();
      notice = `Deleted ${count} ${plural(count, "item", "items")} for good.`;
    } catch (e) {
      if (!signedOut(e)) dialogError = messageOf(e);
    } finally {
      busy = false;
    }
    await load();
  }

  // The server gives UTC times with their offset; the browser shows them in its own time zone.
  function formatTime(value: string, dateOnly = false): string {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return dateOnly ? date.toLocaleDateString() : date.toLocaleString();
  }

  // The folder an item was deleted from.
  function folderOf(item: TrashItem): string {
    if (item.original_path === null) return "Not known";
    const cut = item.original_path.lastIndexOf("/");
    return cut > 0 ? item.original_path.slice(0, cut) : "/";
  }

  // What a screen reader says for an item's checkbox: items of the same name
  // differ in where and when they were deleted (ADR-0197).
  function checkboxLabel(item: TrashItem): string {
    const from = item.original_path === null ? "an unknown folder" : folderOf(item);
    return `Select ${item.name}, deleted from ${from} on ${formatTime(item.deleted)}`;
  }

  function iconOf(item: TrashItem) {
    if (item.type === "directory") return Folder;
    if (item.type === "symlink") return Link;
    return File;
  }
</script>

<div class="page-container">
  <div class="page-header">
    <h1 class="page-title flex items-center gap-2">
      <Trash class="w-7 h-7" />
      Trash
    </h1>
    <div class="flex items-center gap-2">
      <Button variant="secondary" icon={ArrowLeft} href="/files">Files</Button>
      <!-- The span shows the tooltip: Button does not pass a title on. -->
      <span class="flex" title="Refresh">
        <Button variant="secondary" size="icon" disabled={loading} on:click={load}>
          <RefreshCw class="w-5 h-5 {loading ? 'animate-spin' : ''}" />
          <span class="sr-only">Refresh</span>
        </Button>
      </span>
      <Button variant="danger" icon={Trash2} disabled={busy || items.length === 0} on:click={() => ask("everything")}>
        Empty trash
      </Button>
    </div>
  </div>

  <Card>
    <p class="text-sm text-secondary">
      Items deleted in Files wait here, in the trash of their pool or volume, and are deleted for good
      {retentionDays ?? "a number of"} days after they were deleted. Until then they take up space in their
      pool: delete them for good to free it at once. Restore puts an item back in the folder it was deleted
      from; it stays here while that folder is gone or its name is taken there.
    </p>
  </Card>

  {#if loadError}
    <Alert variant="error" title="Could not load the trash">{loadError}</Alert>
  {/if}
  {#each problems as problem}
    <Alert variant="warning" title="A trash could not be read">{problem}</Alert>
  {/each}
  {#if error}
    <!-- role="alert": screen readers announce the message when it appears. -->
    <div role="alert">
      <Alert variant="error" dismissible on:dismiss={() => (error = "")}>{error}</Alert>
    </div>
  {/if}
  {#if notice}
    <div role="status">
      <Alert variant="success" dismissible on:dismiss={() => (notice = "")}>{notice}</Alert>
    </div>
  {/if}

  <Card>
    <div class="mb-4 flex flex-wrap items-center gap-2" role="toolbar" aria-label="Selected items">
      <span class="text-sm text-content-secondary mr-2">{selected.size} selected</span>
      <Button
        variant="secondary"
        size="sm"
        icon={RotateCcw}
        loading={restoring}
        disabled={busy || selected.size === 0}
        on:click={handleRestore}
      >
        Restore
      </Button>
      <Button
        variant="danger"
        size="sm"
        icon={Trash2}
        disabled={busy || selected.size === 0}
        on:click={() => ask("selected")}
      >
        Delete for good
      </Button>
    </div>

    {#if loading && items.length === 0}
      <p class="loading-text">Loading...</p>
    {:else if items.length === 0}
      <EmptyState icon={Trash} title="The trash is empty" description="What you delete in Files comes here." />
    {:else}
      <div class="overflow-x-auto">
        <table class="table" aria-label="Deleted items">
          <thead class="table-header">
            <tr>
              <th class="w-8"><span class="sr-only">Selected</span></th>
              <th>Name</th>
              <th>Deleted from</th>
              <th>Deleted</th>
              <th>Kept until</th>
              <th>Size</th>
            </tr>
          </thead>
          <tbody>
            {#each items as item (item.path)}
              <tr class="table-row align-top">
                <td>
                  <input
                    type="checkbox"
                    aria-label={checkboxLabel(item)}
                    checked={selected.has(item.path)}
                    on:change={() => toggle(item.path)}
                    class="w-4 h-4 rounded bg-surface-overlay border-border"
                  />
                </td>
                <td>
                  <div class="flex items-center gap-2 min-w-0">
                    <svelte:component this={iconOf(item)} class="w-4 h-4 flex-shrink-0 text-content-secondary" />
                    <span class="break-all">{item.name}</span>
                  </div>
                </td>
                <td class="mono-xs break-all">{folderOf(item)}</td>
                <td class="whitespace-nowrap">{formatTime(item.deleted)}</td>
                <td class="whitespace-nowrap">{formatTime(item.expires, true)}</td>
                <td class="whitespace-nowrap">{item.type === "directory" ? "-" : formatSize(item.size)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </Card>
</div>

<Modal
  open={confirming !== null}
  title={confirming === "everything" ? "Empty trash" : "Delete for good"}
  danger
  closable={!busy}
  on:close={closeDialog}
>
  <div class="space-y-3">
    <p class="text-sm">
      {#if confirming === "everything"}
        Delete everything in the trash for good, in every pool and volume? This cannot be undone.
      {:else}
        Delete the {selected.size} selected {plural(selected.size, "item", "items")} for good? This cannot be
        undone.
      {/if}
    </p>
    {#if dialogError}
      <div role="alert">
        <Alert variant="error" title="Could not delete">{dialogError}</Alert>
      </div>
    {/if}
  </div>

  <svelte:fragment slot="footer">
    <span bind:this={cancelSlot} class="contents">
      <Button variant="secondary" class="flex-1" disabled={busy} on:click={closeDialog}>Cancel</Button>
    </span>
    <Button variant="danger" class="flex-1" icon={Trash2} loading={busy} on:click={handleDeleteForGood}>
      {confirming === "everything" ? "Empty trash" : "Delete for good"}
    </Button>
  </svelte:fragment>
</Modal>
