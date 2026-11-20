<script lang="ts">
  // The backups of System > Backup & Restore, newest first as the server lists them (ADR-0432): a
  // table whose rows become cards on a phone (table-stack); each row's actions in one line, Delete
  // as an icon and Restore and Download in its ⋯ menu.
  import { formatBytes, type BackupInfo } from "$lib/api/backup";
  import { ActionMenu, Badge, Button, EmptyState, Skeleton } from "$lib/components/ui";
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

<section class="card card-flush" aria-label={$t("backup.listLabel")}>
  {#if loading}
    <div class="p-5" aria-busy="true"><Skeleton count={3} /></div>
  {:else if backups.length === 0}
    <EmptyState icon={Archive} compact title={$t("backup.empty")} description={$t("backup.emptyHint")} />
  {:else}
    <div class="table-frame">
      <table class="table table-stack">
        <thead class="table-header">
          <tr>
            <th scope="col">{$t("backup.column.name")}</th>
            <th scope="col">{$t("backup.column.created")}</th>
            <th scope="col" class="num">{$t("backup.column.size")}</th>
            <th scope="col">{$t("backup.column.version")}</th>
            <th scope="col"><span class="sr-only">{$t("backup.column.actions")}</span></th>
          </tr>
        </thead>
        <tbody>
          {#each backups as backup (backup.filename)}
            <tr class="table-row">
              <td class="min-w-[16ch]">
                <span class="flex items-start gap-2 min-w-0">
                  <Archive class="w-4 h-4 mt-0.5 shrink-0 text-content-secondary" aria-hidden="true" />
                  <span class="min-w-0 break-all">{backup.filename}</span>
                </span>
              </td>
              <td data-label={$t("backup.column.created")} class="whitespace-nowrap">{formatDateTime(backup.created, $intlLocale)}</td>
              <td data-label={$t("backup.column.size")} class="num">{formatBytes(backup.size, $intlLocale, sizeUnits)}</td>
              <td data-label={$t("backup.column.version")}>
                <span class="inline-flex flex-wrap items-center gap-1.5">
                  {backup.version === "unknown"
                    ? $t("backup.versionUnknown")
                    : $t("backup.version", { version: backup.version })}
                  {#if backup.encrypted}
                    <Badge variant="outline"><Lock class="w-3 h-3" aria-hidden="true" />{$t("backup.encrypted")}</Badge>
                  {/if}
                </span>
              </td>
              <td>
                <div class="table-actions">
                  <Button
                    variant="ghost-danger"
                    size="icon-sm"
                    icon={Trash2}
                    onclick={() => ondelete(backup.filename)}
                    title={$t("backup.delete")}
                    aria-label={$t("backup.deleteOne", { name: backup.filename })}
                  ></Button>
                  <ActionMenu
                    name={backup.filename}
                    items={[
                      { label: $t("backup.restore"), icon: RotateCcw, onclick: () => onrestore(backup) },
                      { label: $t("backup.download"), icon: Download, onclick: () => ondownload(backup.filename) },
                    ]}
                  />
                </div>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</section>
