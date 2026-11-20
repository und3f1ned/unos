<script lang="ts">
  // The trash of every pool and volume (ADR-0259): what Delete in Files moved
  // there, until it is restored, deleted for good, or deleted for good by the
  // daily expiry.
  import { onMount, tick } from "svelte";
  import { deleteFromTrash, emptyTrash, listTrash, restoreFromTrash, type TrashItem } from "$lib/api/files";
  import { SelectionBar, TrashTable } from "$lib/components/files";
  import { messageOf, signedOut } from "$lib/components/files/errors";
  import { toggled } from "$lib/components/files/selection";
  import { Alert, Button, Card, EmptyState, Modal, Skeleton } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import { RefreshCw, RotateCcw, Trash, Trash2 } from "lucide-svelte";

  let items = $state.raw<TrashItem[]>([]);
  let retentionDays: number | null = $state(null);
  // The trashes that could not be read, each with the reason.
  let problems = $state.raw<string[]>([]);
  let loading = $state(true);
  let loadError = $state("");
  // The refusal of a restore, and what the last action did.
  let error = $state("");
  let notice = $state("");
  let selected = $state.raw<Set<string>>(new Set());
  // A restore, a deletion for good or the emptying is on its way.
  let busy = $state(false);
  let restoring = $state(false);
  // The confirmation of a deletion for good: of the selected items, or of everything.
  let confirming: "selected" | "everything" | null = $state(null);
  let dialogError = $state("");
  // Each dialog's Cancel, which takes the keyboard focus: what the dialogs do cannot be undone.
  let cancelSlot: HTMLSpanElement | null = null;

  onMount(load);

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
    selected = toggled(selected, path);
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

  // Sizes in the units of the page's language.
  let units = $derived([$t("files.unit.b"), $t("files.unit.kb"), $t("files.unit.mb"), $t("files.unit.gb"), $t("files.unit.tb")]);
</script>

<div class="page-container">
  <div class="page-header">
    <div class="min-w-0">
      <h1 class="page-title">{$t("files.trash.title")}</h1>
      <!-- How long the trash keeps what it holds, in one line (ADR-0432). -->
      <p class="page-subtitle">
        {retentionDays === null ? $t("files.trash.keptUnknown") : $t("files.trash.kept", { n: retentionDays })}
      </p>
    </div>
    <div class="page-actions">
      <Button variant="secondary" size="icon" disabled={loading} title={$t("common.refresh")} onclick={load}>
        <RefreshCw class="w-4 h-4 {loading ? 'animate-spin' : ''}" aria-hidden="true" />
        <span class="sr-only">{$t("common.refresh")}</span>
      </Button>
      <!-- Not a solid red button: the red one is the confirmation's (ADR-0432). -->
      <Button variant="ghost-danger" icon={Trash2} disabled={busy || items.length === 0} onclick={() => ask("everything")}>
        {$t("files.trash.emptyAction")}
      </Button>
    </div>
  </div>

  {#if loadError}
    <Alert variant="error" title={$t("files.trash.loadError")}>{loadError}</Alert>
  {/if}
  {#if problems.length > 0}
    <!-- The trashes that could not be read, in one alert. -->
    <Alert variant="warning" size="sm" title={$t("files.trash.problem")}>
      <ul class="space-y-0.5">
        {#each problems as problem}
          <li class="break-words">{problem}</li>
        {/each}
      </ul>
    </Alert>
  {/if}
  {#if error}
    <!-- role="alert": screen readers announce the message when it appears. -->
    <div role="alert">
      <Alert variant="error" dismissible ondismiss={() => (error = "")}>{error}</Alert>
    </div>
  {/if}
  {#if notice}
    <div role="status">
      <Alert variant="success" dismissible ondismiss={() => (notice = "")}>{notice}</Alert>
    </div>
  {/if}

  <Card>
    <SelectionBar count={selected.size} hint={$t("files.trash.selectHint")}>
      <Button
        variant="secondary"
        size="sm"
        icon={RotateCcw}
        loading={restoring}
        disabled={busy}
        onclick={handleRestore}
      >
        {$t("files.trash.restore")}
      </Button>
      <Button
        variant="ghost-danger"
        size="sm"
        icon={Trash2}
        disabled={busy}
        onclick={() => ask("selected")}
      >
        {$t("files.deleteForGoodAction")}
      </Button>
    </SelectionBar>

    {#if loading && items.length === 0}
      <div class="card" aria-busy="true">
        <Skeleton shape="row" columns={5} count={4} />
      </div>
    {:else if items.length === 0}
      <EmptyState icon={Trash} title={$t("files.trash.emptyTitle")} description={$t("files.trash.emptyHint")} />
    {:else}
      <TrashTable {items} {selected} {units} ontoggle={toggle} />
    {/if}
  </Card>
</div>

<Modal
  open={confirming !== null}
  title={confirming === "everything" ? $t("files.trash.empty") : $t("files.deleteForGood")}
  danger
  closable={!busy}
  onclose={closeDialog}
>
  <div class="space-y-3">
    <p class="text-sm">
      {#if confirming === "everything"}
        {$t("files.trash.confirmEmpty")}
        {$t("files.trash.space")}
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

  {#snippet footer()}
    <span bind:this={cancelSlot} class="contents">
      <Button variant="secondary" class="flex-1" disabled={busy} onclick={closeDialog}>{$t("common.cancel")}</Button>
    </span>
    <Button variant="danger" class="flex-1" icon={Trash2} loading={busy} onclick={handleDeleteForGood}>
      {confirming === "everything" ? $t("files.trash.empty") : $t("files.deleteForGood")}
    </Button>
  {/snippet}
</Modal>
