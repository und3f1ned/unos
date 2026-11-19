<script module lang="ts">
  /** What a restore did: the server's message and the items it restored, or why it failed. */
  export type RestoreOutcome = { success: boolean; message: string; restored?: string[] };
</script>

<script lang="ts">
  // The dialog of a restore from a backup: the user types the backup's name, and its passphrase
  // for an encrypted one (.tar.gz.enc), then sees what the restore did. The page shows it while
  // it is open, so that its fields start empty every time.
  import type { BackupInfo } from "$lib/api/backup";
  import { Button, Input } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import { AlertTriangle, CheckCircle, RefreshCw, RotateCcw } from "lucide-svelte";

  type Props = {
    target: BackupInfo;
    // The end of the restore; null until it ends.
    result: RestoreOutcome | null;
    // The backup being restored.
    restoring: string | null;
    onclose: () => void;
    onrestore: (passphrase: string) => void;
  };

  let { target, result, restoring, onclose, onrestore }: Props = $props();

  let confirmName = $state("");
  // Encrypted backups (.tar.gz.enc) are restored with their passphrase.
  let passphrase = $state("");

  // The items of a restore that the server names ("restored" of POST /backup/restore): keys of
  // the catalog, by item.
  const RESTORED_ITEMS: Record<string, string> = {
    database: "backup.dialog.items.database",
    smb_config: "backup.dialog.items.smbConfig"
  };

  /** The label of a restored item in the language of the page; an item without one as it comes. */
  function itemLabel(item: string): string {
    return Object.prototype.hasOwnProperty.call(RESTORED_ITEMS, item) ? $t(RESTORED_ITEMS[item]) : item;
  }
</script>

<!-- Modal's overlay (ADR-0302): taller than the window, the dialog scrolls with it, so that its
     buttons stay reachable; my-auto centers it while it fits. -->
<div class="modal-overlay items-start overflow-y-auto p-4">
  <div class="bg-surface-elevated rounded-lg p-6 max-w-md w-full my-auto" role="dialog" aria-modal="true" aria-label={$t("backup.dialog.label")}>
    {#if result}
      <!-- Result view -->
      <div class="text-center">
        {#if result.success}
          <CheckCircle class="w-12 h-12 text-status-success mx-auto mb-4" />
          <h3 class="text-lg font-bold mb-2">{$t("backup.dialog.complete")}</h3>
          <p class="text-content-secondary text-sm mb-4">{result.message}</p>
          {#if result.restored && result.restored.length > 0}
            <div class="bg-surface-overlay rounded p-3 mb-4 text-left">
              <p class="text-xs text-content-secondary mb-2">{$t("backup.dialog.restored")}</p>
              <ul class="text-sm space-y-1">
                {#each result.restored as item}
                  <li class="text-status-success">- {itemLabel(item)}</li>
                {/each}
              </ul>
            </div>
          {/if}
        {:else}
          <AlertTriangle class="w-12 h-12 text-status-error mx-auto mb-4" />
          <h3 class="text-lg font-bold mb-2">{$t("backup.dialog.failed")}</h3>
          <p class="text-status-error text-sm mb-4">{result.message}</p>
        {/if}
        <Button variant="secondary" onclick={onclose}>
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
            {$t("backup.dialog.from")} <span class="text-content font-medium">{target.filename}</span>
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

      {#if target.encrypted}
        <Input
          class="mb-4"
          id="passphrase"
          type="password"
          label={$t("backup.dialog.passphrase")}
          autocomplete="off"
          required
          bind:value={passphrase}
        />
      {/if}

      <div class="mb-4">
        <label for="confirmName" class="block text-sm text-content-secondary mb-2">
          {$t("backup.dialog.confirm")}
        </label>
        <input
          type="text"
          id="confirmName"
          bind:value={confirmName}
          placeholder={target.filename}
          class="form-input text-sm"
        />
      </div>

      <div class="flex gap-3">
        <Button variant="secondary" class="flex-1" onclick={onclose}>
          {$t("common.cancel")}
        </Button>
        <Button variant="warning" class="flex-1"
          onclick={() => onrestore(passphrase)}
          disabled={confirmName !== target.filename ||
            (target.encrypted && !passphrase) ||
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
