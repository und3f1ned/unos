<script lang="ts">
  // The backups of System > Backup & Restore, newest first as the server lists them, each with
  // Restore, Download and Delete.
  import { formatBytes, type BackupInfo } from "$lib/api/backup";
  import { Button, EmptyState, Skeleton } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { formatDateTime } from "$lib/time";
  import { Archive, Download, Lock, RotateCcw, Trash2 } from "lucide-svelte";

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

<div class="card">
  {#if loading}
    <div aria-busy="true"><Skeleton shape="row" count={3} /></div>
  {:else if backups.length === 0}
    <EmptyState compact icon={Archive} title={$t("backup.empty")} description={$t("backup.emptyHint")} />
  {:else}
    <ul class="divide-y divide-border" aria-label={$t("backup.list")}>
      {#each backups as backup}
        <li class="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 py-3 first:pt-0 last:pb-0">
          <div class="flex min-w-0 flex-1 basis-64 items-start gap-3">
            <Archive class="mt-0.5 h-5 w-5 shrink-0 text-content-secondary" aria-hidden="true" />
            <div class="min-w-0">
              <p class="font-medium text-sm break-all">{backup.filename}</p>
              <p class="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-content-secondary tabular">
                <span>{formatDateTime(backup.created, $intlLocale, { dateStyle: "medium", timeStyle: "short" })}</span>
                <span>{formatBytes(backup.size, $intlLocale, sizeUnits)}</span>
                <span>
                  {backup.version === "unknown"
                    ? $t("backup.versionUnknown")
                    : $t("backup.version", { version: backup.version })}
                </span>
                {#if backup.encrypted}
                  <span class="inline-flex items-center gap-1"><Lock class="h-3 w-3" aria-hidden="true" />{$t("backup.encrypted")}</span>
                {/if}
              </p>
            </div>
          </div>
          <!-- Restore in words, so that its icon is not taken for Refresh; Download and Delete as
               icons with their names as tooltips. -->
          <div class="flex shrink-0 items-center gap-1">
            <Button variant="secondary" size="sm" icon={RotateCcw}
              onclick={() => onrestore(backup)}
              aria-label={$t("backup.restoreOne", { name: backup.filename })}
            >
              {$t("backup.restoreAction")}
            </Button>
            <Button variant="ghost" size="icon-sm"
              onclick={() => ondownload(backup.filename)}
              title={$t("backup.download")}
              aria-label={$t("backup.downloadOne", { name: backup.filename })}
            >
              <Download class="w-4 h-4" />
            </Button>
            <Button variant="ghost-danger" size="icon-sm"
              onclick={() => ondelete(backup.filename)}
              title={$t("backup.delete")}
              aria-label={$t("backup.deleteOne", { name: backup.filename })}
            >
              <Trash2 class="w-4 h-4" />
            </Button>
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</div>
