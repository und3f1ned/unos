<script lang="ts">
  import { messageOf } from "$lib/errors";
  import { onMount, tick } from "svelte";
  import { ApiError } from "$lib/api/client";
  import { Alert, Button } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import { listBackups, createBackup, deleteBackup, downloadBackup, uploadBackup, restoreBackup, type BackupInfo } from "$lib/api/backup";
  import BackupList from "$lib/components/system/backup/BackupList.svelte";
  import RestoreBackupDialog, { type RestoreOutcome } from "$lib/components/system/backup/RestoreBackupDialog.svelte";
  import { Plus, RefreshCw, Upload } from "lucide-svelte";
  import OffsiteCopy from "./OffsiteCopy.svelte";

  let backups = $state<BackupInfo[]>([]);
  let loading = $state(true);
  let creating = $state(false);
  let uploading = $state(false);
  let restoring = $state<string | null>(null);

  // Restore confirmation modal: its fields are its own.
  let showRestoreModal = $state(false);
  let restoreTarget = $state<BackupInfo | null>(null);
  let restoreResult = $state<RestoreOutcome | null>(null);
  // The last failed action, shown above the list until it is dismissed.
  let error = $state<{ title: string; message: string } | null>(null);
  let errorBox = $state<HTMLDivElement>();

  // The units of a backup's size in the language of the page, from bytes to gigabytes (formatBytes),
  // for the list and the off-device copy.
  let sizeUnits = $derived([
    $t("backup.size.bytes"),
    $t("backup.size.kilobytes"),
    $t("backup.size.megabytes"),
    $t("backup.size.gigabytes")
  ]);

  // A 401 has already ended the session and the sign-in page is on its way.
  function sessionEnded(e: unknown): boolean {
    return e instanceof ApiError && e.status === 401;
  }

  function errorMessage(e: unknown): string {
    return messageOf(e);
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
  let fileInput: HTMLInputElement | undefined;

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
    restoreResult = null;
    showRestoreModal = true;
  }

  function closeRestoreModal() {
    showRestoreModal = false;
    restoreTarget = null;
    restoreResult = null;
  }

  async function handleRestore(passphrase: string) {
    if (!restoreTarget) return;

    restoring = restoreTarget.filename;
    try {
      const result = await restoreBackup(
        restoreTarget.filename,
        restoreTarget.encrypted ? passphrase : undefined
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
    <h2 class="text-xl font-semibold">{$t("backup.title")}</h2>
    <div class="flex flex-wrap gap-2">
      <!-- Encrypted backups (.tar.gz.enc) are checked with their passphrase when restored. -->
      <input
        type="file"
        accept=".tar.gz,.tar.gz.enc"
        class="hidden"
        bind:this={fileInput}
        onchange={handleFileSelected}
      />
      <Button variant="secondary" size="sm"
        onclick={handleUploadClick}
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
        onclick={handleCreate}
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
      <Alert variant="error" title={error.title} dismissible ondismiss={() => (error = null)}>
        {error.message}
      </Alert>
    </div>
  {/if}

  <BackupList
    {backups}
    {loading}
    {sizeUnits}
    onrestore={openRestoreModal}
    ondownload={handleDownload}
    ondelete={handleDelete}
  />

  <!-- The off-device copy (ADR-0337): a configuration it restores lands in the list above. -->
  <OffsiteCopy {sizeUnits} onrestored={loadBackups} />
</div>

<!-- Restore Confirmation Modal -->
{#if showRestoreModal && restoreTarget}
  <RestoreBackupDialog
    target={restoreTarget}
    result={restoreResult}
    {restoring}
    onclose={closeRestoreModal}
    onrestore={handleRestore}
  />
{/if}
