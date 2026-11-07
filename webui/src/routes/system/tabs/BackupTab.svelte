<script lang="ts">
  import { onMount, tick } from "svelte";
  import { ApiError } from "$lib/api/client";
  import { Alert, Input } from "$lib/components/ui";
  import { listBackups, createBackup, deleteBackup, downloadBackup, uploadBackup, restoreBackup, formatBytes, type BackupInfo } from "$lib/api/backup";
  import { Archive, Plus, Trash2, RefreshCw, Clock, Download, Upload, RotateCcw, AlertTriangle, CheckCircle, Lock } from "lucide-svelte";

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
      showError("Could not load the backups", e);
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
      showError("Could not create a backup", e);
    }
    creating = false;
  }

  async function handleDelete(filename: string) {
    if (!confirm("Delete backup " + filename + "?")) return;
    error = null;
    try {
      await deleteBackup(filename);
      await loadBackups();
    } catch (e) {
      showError(`Could not delete ${filename}`, e);
    }
  }

  async function handleDownload(filename: string) {
    error = null;
    try {
      await downloadBackup(filename);
    } catch (e) {
      showError(`Could not download ${filename}`, e);
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
      showError(`Could not upload ${file.name}`, e);
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
        restoreResult = { success: false, message: errorMessage(e) || "Failed to restore backup" };
      }
    }
    restoring = null;
  }
</script>

<div class="space-y-4">
  <div class="flex items-center justify-between">
    <h2 class="text-xl font-bold">Backup and Restore</h2>
    <div class="flex gap-2">
      <!-- Encrypted backups (.tar.gz.enc) are checked with their passphrase when restored. -->
      <input
        type="file"
        accept=".tar.gz,.tar.gz.enc"
        class="hidden"
        bind:this={fileInput}
        on:change={handleFileSelected}
      />
      <button
        on:click={handleUploadClick}
        class="flex items-center gap-2 px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm"
        disabled={uploading}
      >
        {#if uploading}
          <RefreshCw class="w-4 h-4 animate-spin" />
        {:else}
          <Upload class="w-4 h-4" />
        {/if}
        Upload Backup
      </button>
      <button
        on:click={handleCreate}
        class="flex items-center gap-2 px-3 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm"
        disabled={creating}
      >
        {#if creating}
          <RefreshCw class="w-4 h-4 animate-spin" />
        {:else}
          <Plus class="w-4 h-4" />
        {/if}
        Create Backup
      </button>
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

  <div class="bg-slate-800 rounded-lg p-4">
    {#if loading}
      <div class="flex justify-center py-8">
        <RefreshCw class="w-6 h-6 animate-spin text-blue-500" />
      </div>
    {:else if backups.length === 0}
      <div class="text-center py-8 text-slate-400">
        <Archive class="w-10 h-10 mx-auto mb-2 opacity-50" />
        <p>No backups yet</p>
        <p class="text-sm mt-1">Create a backup to save your UNOS configuration</p>
      </div>
    {:else}
      <div class="space-y-2">
        {#each backups as backup}
          <div class="flex items-center justify-between p-3 bg-slate-700 rounded-lg">
            <div class="flex items-center gap-3">
              <Archive class="w-8 h-8 text-blue-400" />
              <div>
                <p class="font-medium text-sm">{backup.filename}</p>
                <p class="text-xs text-slate-400 flex items-center gap-2">
                  <Clock class="w-3 h-3" />
                  {new Date(backup.created).toLocaleString()}
                  <span class="text-slate-500">|</span>
                  {formatBytes(backup.size)}
                  <span class="text-slate-500">|</span>
                  v{backup.version}
                  {#if backup.encrypted}
                    <span class="text-slate-500">|</span>
                    <span class="flex items-center gap-1"><Lock class="w-3 h-3" />encrypted</span>
                  {/if}
                </p>
              </div>
            </div>
            <div class="flex items-center gap-1">
              <button
                on:click={() => openRestoreModal(backup)}
                class="p-2 text-green-400 hover:bg-slate-600 rounded"
                title="Restore"
                aria-label="Restore {backup.filename}"
              >
                <RotateCcw class="w-4 h-4" />
              </button>
              <button
                on:click={() => handleDownload(backup.filename)}
                class="p-2 text-blue-400 hover:bg-slate-600 rounded"
                title="Download"
                aria-label="Download {backup.filename}"
              >
                <Download class="w-4 h-4" />
              </button>
              <button
                on:click={() => handleDelete(backup.filename)}
                class="p-2 text-red-400 hover:bg-slate-600 rounded"
                title="Delete"
                aria-label="Delete {backup.filename}"
              >
                <Trash2 class="w-4 h-4" />
              </button>
            </div>
          </div>
        {/each}
      </div>
    {/if}
  </div>
</div>

<!-- Restore Confirmation Modal -->
{#if showRestoreModal && restoreTarget}
  <div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
    <div class="bg-slate-800 rounded-lg p-6 max-w-md w-full mx-4" role="dialog" aria-modal="true" aria-label="Restore backup">
      {#if restoreResult}
        <!-- Result view -->
        <div class="text-center">
          {#if restoreResult.success}
            <CheckCircle class="w-12 h-12 text-green-500 mx-auto mb-4" />
            <h3 class="text-lg font-bold mb-2">Restore Complete</h3>
            <p class="text-slate-400 text-sm mb-4">{restoreResult.message}</p>
            {#if restoreResult.restored && restoreResult.restored.length > 0}
              <div class="bg-slate-700 rounded p-3 mb-4 text-left">
                <p class="text-xs text-slate-400 mb-2">Restored items:</p>
                <ul class="text-sm space-y-1">
                  {#each restoreResult.restored as item}
                    <li class="text-green-400">- {item}</li>
                  {/each}
                </ul>
              </div>
            {/if}
          {:else}
            <AlertTriangle class="w-12 h-12 text-red-500 mx-auto mb-4" />
            <h3 class="text-lg font-bold mb-2">Restore Failed</h3>
            <p class="text-red-400 text-sm mb-4">{restoreResult.message}</p>
          {/if}
          <button
            on:click={closeRestoreModal}
            class="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg"
          >
            Close
          </button>
        </div>
      {:else}
        <!-- Confirmation view -->
        <div class="flex items-start gap-3 mb-4">
          <AlertTriangle class="w-6 h-6 text-yellow-500 flex-shrink-0 mt-1" />
          <div>
            <h3 class="text-lg font-bold">Restore Backup</h3>
            <p class="text-slate-400 text-sm mt-1">
              This will restore configuration from <span class="text-white font-medium">{restoreTarget.filename}</span>
            </p>
          </div>
        </div>

        <div class="bg-slate-700 rounded p-3 mb-4">
          <p class="text-yellow-400 text-sm">Warning: This will overwrite current settings including:</p>
          <ul class="text-sm text-slate-300 mt-2 space-y-1">
            <li>- Database (user accounts with their passwords, two-factor authentication, API tokens and the login history)</li>
            <li>- SMB share configuration</li>
          </ul>
        </div>

        {#if restoreTarget.encrypted}
          <Input
            class="mb-4"
            id="restorePassphrase"
            type="password"
            label="Passphrase of the encrypted backup"
            autocomplete="off"
            required
            bind:value={restorePassphrase}
          />
        {/if}

        <div class="mb-4">
          <label for="restoreConfirmName" class="block text-sm text-slate-400 mb-2">
            Type the backup filename to confirm:
          </label>
          <input
            type="text"
            id="restoreConfirmName"
            bind:value={restoreConfirmName}
            placeholder={restoreTarget.filename}
            class="w-full px-3 py-2 bg-slate-900 border border-slate-600 rounded-lg focus:border-blue-500 focus:outline-none text-sm"
          />
        </div>

        <div class="flex gap-3">
          <button
            on:click={closeRestoreModal}
            class="flex-1 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg"
          >
            Cancel
          </button>
          <button
            on:click={handleRestore}
            disabled={restoreConfirmName !== restoreTarget.filename ||
              (restoreTarget.encrypted && !restorePassphrase) ||
              restoring !== null}
            class="flex-1 px-4 py-2 bg-yellow-600 hover:bg-yellow-500 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg flex items-center justify-center gap-2"
          >
            {#if restoring}
              <RefreshCw class="w-4 h-4 animate-spin" />
              Restoring...
            {:else}
              <RotateCcw class="w-4 h-4" />
              Restore
            {/if}
          </button>
        </div>
      {/if}
    </div>
  </div>
{/if}
