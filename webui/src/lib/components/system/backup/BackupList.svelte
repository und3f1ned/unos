<script lang="ts">
  // The backups of System > Backup & Restore, newest first as the server lists them, each with
  // Restore, Download and Delete.
  import { formatBytes, type BackupInfo } from "$lib/api/backup";
  import { Button } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { formatDateTime } from "$lib/time";
  import { Archive, Clock, Download, Lock, RefreshCw, RotateCcw, Trash2 } from "lucide-svelte";

  type Props = {
    backups: BackupInfo[];
    loading: boolean;
    // The units of a backup's size in the language of the page (formatBytes).
    sizeUnits: string[];
    onrestore: (backup: BackupInfo) => void;
    ondownload: (filename: string) => void;
    ondelete: (filename: string) => void;
  };

  let { backups, loading, sizeUnits, onrestore, ondownload, ondelete }: Props = $props();
</script>

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
              onclick={() => onrestore(backup)}
              title={$t("backup.restore")}
              aria-label={$t("backup.restoreOne", { name: backup.filename })}
            >
              <RotateCcw class="w-4 h-4" />
            </Button>
            <Button variant="ghost" size="icon" class="text-status-info hover:text-status-info"
              onclick={() => ondownload(backup.filename)}
              title={$t("backup.download")}
              aria-label={$t("backup.downloadOne", { name: backup.filename })}
            >
              <Download class="w-4 h-4" />
            </Button>
            <Button variant="ghost" size="icon" class="text-status-error hover:text-status-error"
              onclick={() => ondelete(backup.filename)}
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
