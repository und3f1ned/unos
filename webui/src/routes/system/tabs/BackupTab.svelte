<script lang="ts">
  import { onMount, tick } from "svelte";
  import { ApiError } from "$lib/api/client";
  import { Alert, Input, Button } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { formatDateTime } from "$lib/time";
  import { listBackups, createBackup, deleteBackup, downloadBackup, uploadBackup, restoreBackup, formatBytes, type BackupInfo } from "$lib/api/backup";
  import { Archive, Plus, Trash2, RefreshCw, Clock, Download, Upload, RotateCcw, AlertTriangle, CheckCircle, Lock } from "lucide-svelte";
  import OffsiteCopy from "./OffsiteCopy.svelte";

  let backups: BackupInfo[] = [];
  let loading = true;
  let creating = false;
  let uploading = false;
  let restoring: string | null = null;

  // Restore confirmation modal
  let showRestoreModal = false;
  let restoreTarget: BackupInfo | null = null;
  let restoreConfirmName = "";
  // Encrypted backups (.tar.gz.enc) are restored with their passphrase.
  let restorePassphrase = "";
  let restoreResult: { success: boolean; message: string; restored?: string[] } | null = null;
  // The last failed action, shown above the list until it is dismissed.
  let error: { title: string; message: string } | null = null;
  let errorBox: HTMLDivElement | undefined;

  // The items of a restore that the server names ("restored" of POST /backup/restore): keys of
  // the catalog, by item.
  const RESTORED_ITEMS: Record<string, string> = {
    database: "backup.dialog.items.database",
    smb_config: "backup.dialog.items.smbConfig"
  };

  /** The label of a restored item in the language of the page; an item without one as it comes. */
  $: itemLabel = (item: string): string =>
    Object.prototype.hasOwnProperty.call(RESTORED_ITEMS, item) ? $t(RESTORED_ITEMS[item]) : item;

  // The units of a backup's size in the language of the page, from bytes to gigabytes (formatBytes).
  $: sizeUnits = [
    $t("backup.size.bytes"),
    $t("backup.size.kilobytes"),
    $t("backup.size.megabytes"),
    $t("backup.size.gigabytes")
  ];

  // A 401 has already ended the session and the sign-in page is on its way.
  function sessionEnded(e: unknown): boolean {
    return e instanceof ApiError && e.status === 401;
  }

  function errorMessage(e: unknown): string {
    return e instanceof Error ? e.message : String(e);
  }

  async function showError(title: string, e: unknown) {
    if (sessionEnded(e)) return;
    error = { title, message: errorMessage(e) };
    // The message is above the list: after a click far down a long list it
    // would be out of sight.
    await tick();
    errorBox?.scrollIntoView({ block: "nearest" });
  }

  // Upload input ref
  let fileInput: HTMLInputElement;

  onMount(() => loadBackups());

  async function loadBackups() {
    loading = true;
    try {
      const result = await listBackups();
      backups = result.backups;
    } catch (e) {
      showError($t("backup.loadFailed"), e);
    }
    loading = false;
  }

  async function handleCreate() {
    creating = true;
    error = null;
    try {
      await createBackup();
      await loadBackups();
    } catch (e) {
      showError($t("backup.createFailed"), e);
    }
    creating = false;
  }

  async function handleDelete(filename: string) {
    if (!confirm($t("backup.confirmDelete", { name: filename }))) return;
    error = null;
    try {
      await deleteBackup(filename);
      await loadBackups();
    } catch (e) {
      showError($t("backup.deleteFailed", { name: filename }), e);
    }
  }

  async function handleDownload(filename: string) {
    error = null;
    try {
      await downloadBackup(filename);
    } catch (e) {
      showError($t("backup.downloadFailed", { name: filename }), e);
    }
  }

  function handleUploadClick() {
    fileInput?.click();
  }

  async function handleFileSelected(event: Event) {
    const target = event.target as HTMLInputElement;
    const file = target.files?.[0];
    if (!file) return;

    uploading = true;
    error = null;
    try {
      await uploadBackup(file);
      await loadBackups();
    } catch (e) {
      showError($t("backup.uploadFailed", { name: file.name }), e);
    }
    uploading = false;
    target.value = "";
  }

  function openRestoreModal(backup: BackupInfo) {
    restoreTarget = backup;
    restoreConfirmName = "";
    restorePassphrase = "";
    restoreResult = null;
    showRestoreModal = true;
  }

  function closeRestoreModal() {
    showRestoreModal = false;
    restoreTarget = null;
    restoreConfirmName = "";
    restorePassphrase = "";
    restoreResult = null;
  }

  async function handleRestore() {
    if (!restoreTarget) return;

    restoring = restoreTarget.filename;
    try {
      const result = await restoreBackup(
        restoreTarget.filename,
        restoreTarget.encrypted ? restorePassphrase : undefined
      );
      restoreResult = {
        success: true,
        message: result.message,
        restored: result.restored
      };
    } catch (e) {
      // Restore failures are shown in the dialog.
      if (!sessionEnded(e)) {
        restoreResult = { success: false, message: errorMessage(e) || $t("backup.dialog.failedFallback") };
      }
    }
    restoring = null;
  }
</script>

<div class="space-y-4">
  <div class="flex flex-wrap items-center justify-between gap-3">
    <h2 class="text-xl font-bold">{$t("backup.title")}</h2>
    <div class="flex flex-wrap gap-2">
      <!-- Encrypted backups (.tar.gz.enc) are checked with their passphrase when restored. -->
      <input
        type="file"
        accept=".tar.gz,.tar.gz.enc"
        class="hidden"
        bind:this={fileInput}
        on:change={handleFileSelected}
      />
      <Button variant="secondary" size="sm"
        on:click={handleUploadClick}
        disabled={uploading}
      >
        {#if uploading}
          <RefreshCw class="w-4 h-4 animate-spin" />
        {:else}
          <Upload class="w-4 h-4" />
        {/if}
        {$t("backup.upload")}
      </Button>
      <Button variant="primary" size="sm"
        on:click={handleCreate}
        disabled={creating}
      >
        {#if creating}
          <RefreshCw class="w-4 h-4 animate-spin" />
        {:else}
          <Plus class="w-4 h-4" />
        {/if}
        {$t("backup.create")}
      </Button>
    </div>
  </div>

  {#if error}
    <!-- role="alert": screen readers announce the message when it appears. -->
    <div bind:this={errorBox} role="alert">
      <Alert variant="error" title={error.title} dismissible on:dismiss={() => (error = null)}>
        {error.message}
      </Alert>
    </div>
  {/if}

  <div class="bg-surface-elevated rounded-lg p-4">
    {#if loading}
      <div class="flex justify-center py-8">
        <RefreshCw class="w-6 h-6 animate-spin text-brand-ring" />
      </div>
    {:else if backups.length === 0}
      <div class="text-center py-8 text-content-secondary">
        <Archive class="w-10 h-10 mx-auto mb-2 opacity-50" />
        <p>{$t("backup.empty")}</p>
        <p class="text-sm mt-1">{$t("backup.emptyHint")}</p>
      </div>
    {:else}
      <div class="space-y-2">
        {#each backups as backup}
          <div class="flex items-center justify-between gap-2 p-3 bg-surface-overlay rounded-lg">
            <div class="flex items-center gap-3 min-w-0">
              <Archive class="w-8 h-8 text-status-info shrink-0" />
              <div class="min-w-0">
                <p class="font-medium text-sm break-all">{backup.filename}</p>
                <p class="text-xs text-content-secondary flex flex-wrap items-center gap-x-2">
                  <Clock class="w-3 h-3" />
                  {formatDateTime(backup.created, $intlLocale)}
                  <span class="text-content-muted">|</span>
                  {formatBytes(backup.size, $intlLocale, sizeUnits)}
                  <span class="text-content-muted">|</span>
                  {backup.version === "unknown"
                    ? $t("backup.versionUnknown")
                    : $t("backup.version", { version: backup.version })}
                  {#if backup.encrypted}
                    <span class="text-content-muted">|</span>
                    <span class="flex items-center gap-1"><Lock class="w-3 h-3" />{$t("backup.encrypted")}</span>
                  {/if}
                </p>
              </div>
            </div>
            <div class="flex shrink-0 items-center gap-1">
              <Button variant="ghost" size="icon" class="text-status-success hover:text-status-success"
                on:click={() => openRestoreModal(backup)}
                title={$t("backup.restore")}
                aria-label={$t("backup.restoreOne", { name: backup.filename })}
              >
                <RotateCcw class="w-4 h-4" />
              </Button>
              <Button variant="ghost" size="icon" class="text-status-info hover:text-status-info"
                on:click={() => handleDownload(backup.filename)}
                title={$t("backup.download")}
                aria-label={$t("backup.downloadOne", { name: backup.filename })}
              >
                <Download class="w-4 h-4" />
              </Button>
              <Button variant="ghost" size="icon" class="text-status-error hover:text-status-error"
                on:click={() => handleDelete(backup.filename)}
                title={$t("backup.delete")}
                aria-label={$t("backup.deleteOne", { name: backup.filename })}
              >
                <Trash2 class="w-4 h-4" />
              </Button>
            </div>
          </div>
        {/each}
      </div>
    {/if}
  </div>

  <!-- The off-device copy (ADR-0337): a configuration it restores lands in the list above. -->
  <OffsiteCopy on:restored={loadBackups} />
</div>

<!-- Restore Confirmation Modal. Modal's overlay (ADR-0302): taller than the window, the dialog
     scrolls with it, so that its buttons stay reachable; my-auto centers it while it fits. -->
{#if showRestoreModal && restoreTarget}
  <div class="modal-overlay items-start overflow-y-auto p-4">
    <div class="bg-surface-elevated rounded-lg p-6 max-w-md w-full my-auto" role="dialog" aria-modal="true" aria-label={$t("backup.dialog.label")}>
      {#if restoreResult}
        <!-- Result view -->
        <div class="text-center">
          {#if restoreResult.success}
            <CheckCircle class="w-12 h-12 text-status-success mx-auto mb-4" />
            <h3 class="text-lg font-bold mb-2">{$t("backup.dialog.complete")}</h3>
            <p class="text-content-secondary text-sm mb-4">{restoreResult.message}</p>
            {#if restoreResult.restored && restoreResult.restored.length > 0}
              <div class="bg-surface-overlay rounded p-3 mb-4 text-left">
                <p class="text-xs text-content-secondary mb-2">{$t("backup.dialog.restored")}</p>
                <ul class="text-sm space-y-1">
                  {#each restoreResult.restored as item}
                    <li class="text-status-success">- {itemLabel(item)}</li>
                  {/each}
                </ul>
              </div>
            {/if}
          {:else}
            <AlertTriangle class="w-12 h-12 text-status-error mx-auto mb-4" />
            <h3 class="text-lg font-bold mb-2">{$t("backup.dialog.failed")}</h3>
            <p class="text-status-error text-sm mb-4">{restoreResult.message}</p>
          {/if}
          <Button variant="secondary"
            on:click={closeRestoreModal}
           
          >
            {$t("backup.dialog.close")}
          </Button>
        </div>
      {:else}
        <!-- Confirmation view -->
        <div class="flex items-start gap-3 mb-4">
          <AlertTriangle class="w-6 h-6 text-status-warning flex-shrink-0 mt-1" />
          <div>
            <h3 class="text-lg font-bold">{$t("backup.dialog.title")}</h3>
            <p class="text-content-secondary text-sm mt-1">
              {$t("backup.dialog.from")} <span class="text-content-on-brand font-medium">{restoreTarget.filename}</span>
            </p>
          </div>
        </div>

        <div class="bg-surface-overlay rounded p-3 mb-4">
          <p class="text-status-warning text-sm">{$t("backup.dialog.warning")}</p>
          <ul class="text-sm text-content-soft mt-2 space-y-1">
            <li>{$t("backup.dialog.database")}</li>
            <li>{$t("backup.dialog.shares")}</li>
          </ul>
        </div>

        {#if restoreTarget.encrypted}
          <Input
            class="mb-4"
            id="restorePassphrase"
            type="password"
            label={$t("backup.dialog.passphrase")}
            autocomplete="off"
            required
            bind:value={restorePassphrase}
          />
        {/if}

        <div class="mb-4">
          <label for="restoreConfirmName" class="block text-sm text-content-secondary mb-2">
            {$t("backup.dialog.confirm")}
          </label>
          <input
            type="text"
            id="restoreConfirmName"
            bind:value={restoreConfirmName}
            placeholder={restoreTarget.filename}
            class="form-input text-sm"
          />
        </div>

        <div class="flex gap-3">
          <Button variant="secondary" class="flex-1"
            on:click={closeRestoreModal}
           
          >
            {$t("common.cancel")}
          </Button>
          <Button variant="warning" class="flex-1"
            on:click={handleRestore}
            disabled={restoreConfirmName !== restoreTarget.filename ||
              (restoreTarget.encrypted && !restorePassphrase) ||
              restoring !== null}
           
          >
            {#if restoring}
              <RefreshCw class="w-4 h-4 animate-spin" />
              {$t("backup.dialog.restoring")}
            {:else}
              <RotateCcw class="w-4 h-4" />
              {$t("backup.restore")}
            {/if}
          </Button>
        </div>
      {/if}
    </div>
  </div>
{/if}
