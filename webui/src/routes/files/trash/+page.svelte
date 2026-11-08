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
  import { intlLocale, t } from "$lib/i18n";
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
          ? $t("files.trash.restoredTo", { path: restored[0] })
          : $t("files.trash.restored", { n: restored.length });
    } catch (e) {
      if (!signedOut(e)) error = $t("files.trash.restoreFailed", { message: messageOf(e) });
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
      notice = $t("files.trash.deleted", { n: count });
    } catch (e) {
      if (!signedOut(e)) dialogError = messageOf(e);
    } finally {
      busy = false;
    }
    await load();
  }

  // The server gives UTC times with their offset; the browser shows them in its own time zone,
  // in the language of the page.
  $: formatTime = (value: string, dateOnly = false): string => {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return dateOnly ? date.toLocaleDateString($intlLocale) : date.toLocaleString($intlLocale);
  };

  // The folder an item was deleted from.
  $: folderOf = (item: TrashItem): string => {
    if (item.original_path === null) return $t("files.trash.notKnown");
    const cut = item.original_path.lastIndexOf("/");
    return cut > 0 ? item.original_path.slice(0, cut) : "/";
  };

  // What a screen reader says for an item's checkbox: items of the same name
  // differ in where and when they were deleted (ADR-0197).
  $: checkboxLabel = (item: TrashItem): string => {
    const time = formatTime(item.deleted);
    return item.original_path === null
      ? $t("files.trash.selectUnknown", { name: item.name, time })
      : $t("files.trash.select", { name: item.name, folder: folderOf(item), time });
  };

  // Sizes in the units of the page's language.
  $: units = [$t("files.unit.b"), $t("files.unit.kb"), $t("files.unit.mb"), $t("files.unit.gb"), $t("files.unit.tb")];

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
      {$t("files.trash.title")}
    </h1>
    <div class="flex flex-wrap items-center gap-2">
      <Button variant="secondary" icon={ArrowLeft} href="/files">{$t("files.trash.files")}</Button>
      <!-- The span shows the tooltip: Button does not pass a title on. -->
      <span class="flex" title={$t("common.refresh")}>
        <Button variant="secondary" size="icon" disabled={loading} on:click={load}>
          <RefreshCw class="w-5 h-5 {loading ? 'animate-spin' : ''}" />
          <span class="sr-only">{$t("common.refresh")}</span>
        </Button>
      </span>
      <Button variant="danger" icon={Trash2} disabled={busy || items.length === 0} on:click={() => ask("everything")}>
        {$t("files.trash.empty")}
      </Button>
    </div>
  </div>

  <Card>
    <p class="text-sm text-secondary">
      {retentionDays === null ? $t("files.trash.keptUnknown") : $t("files.trash.kept", { n: retentionDays })}
      {$t("files.trash.space")}
    </p>
  </Card>

  {#if loadError}
    <Alert variant="error" title={$t("files.trash.loadError")}>{loadError}</Alert>
  {/if}
  {#each problems as problem}
    <Alert variant="warning" title={$t("files.trash.problem")}>{problem}</Alert>
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
    <div class="mb-4 flex flex-wrap items-center gap-2" role="toolbar" aria-label={$t("files.selectedItems")}>
      <span class="text-sm text-content-secondary mr-2">{$t("files.selected", { n: selected.size })}</span>
      <Button
        variant="secondary"
        size="sm"
        icon={RotateCcw}
        loading={restoring}
        disabled={busy || selected.size === 0}
        on:click={handleRestore}
      >
        {$t("files.trash.restore")}
      </Button>
      <Button
        variant="danger"
        size="sm"
        icon={Trash2}
        disabled={busy || selected.size === 0}
        on:click={() => ask("selected")}
      >
        {$t("files.deleteForGood")}
      </Button>
    </div>

    {#if loading && items.length === 0}
      <p class="loading-text">{$t("common.loading")}</p>
    {:else if items.length === 0}
      <EmptyState icon={Trash} title={$t("files.trash.emptyTitle")} description={$t("files.trash.emptyHint")} />
    {:else}
      <div class="table-frame">
        <table class="table" aria-label={$t("files.trash.items")}>
          <thead class="table-header">
            <tr>
              <th class="w-8"><span class="sr-only">{$t("files.trash.column.selected")}</span></th>
              <th>{$t("files.column.name")}</th>
              <th>{$t("files.trash.column.from")}</th>
              <th>{$t("files.trash.column.deleted")}</th>
              <th>{$t("files.trash.column.keptUntil")}</th>
              <th>{$t("files.column.size")}</th>
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
                <td class="whitespace-nowrap">{item.type === "directory" ? "-" : formatSize(item.size, units)}</td>
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
  title={confirming === "everything" ? $t("files.trash.empty") : $t("files.deleteForGood")}
  danger
  closable={!busy}
  on:close={closeDialog}
>
  <div class="space-y-3">
    <p class="text-sm">
      {#if confirming === "everything"}
        {$t("files.trash.confirmEmpty")}
      {:else}
        {$t("files.trash.confirmSelected", { n: selected.size })}
      {/if}
    </p>
    {#if dialogError}
      <div role="alert">
        <Alert variant="error" title={$t("files.trash.deleteFailed")}>{dialogError}</Alert>
      </div>
    {/if}
  </div>

  <svelte:fragment slot="footer">
    <span bind:this={cancelSlot} class="contents">
      <Button variant="secondary" class="flex-1" disabled={busy} on:click={closeDialog}>{$t("common.cancel")}</Button>
    </span>
    <Button variant="danger" class="flex-1" icon={Trash2} loading={busy} on:click={handleDeleteForGood}>
      {confirming === "everything" ? $t("files.trash.empty") : $t("files.deleteForGood")}
    </Button>
  </svelte:fragment>
</Modal>
