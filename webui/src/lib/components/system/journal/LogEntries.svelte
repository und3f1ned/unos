<script lang="ts">
  // The entries of System > Logs, the newest first: each with its time in the browser's time zone,
  // its level, its program and its message, in which the search is marked. With Auto, the tab
  // follows the journal and says so, or why it stopped.
  import type { LogEntry, LogPriority } from "$lib/api/logs";
  import { Alert, Badge, Card, EmptyState } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { apiTime } from "$lib/time";
  import { FileText, RefreshCw } from "lucide-svelte";
  import { priorityVariant, splitByMatch, textOf } from "./logs";

  type Props = {
    entries: LogEntry[];
    loading: boolean;
    // Why the entries could not be read; shown instead of the list.
    error: string;
    // The search, which the messages mark.
    search: string;
    hasActiveFilters: boolean;
    autoRefresh: boolean;
    // Why Auto stopped by itself: the connection was lost, or the server refused it (4403).
    autoStopped: "" | "lost" | "forbidden";
  };

  let { entries, loading, error, search, hasActiveFilters, autoRefresh, autoStopped }: Props = $props();

  // An entry's level in the list.
  const LEVELS: Record<LogPriority, string> = {
    emerg: "logs.level.emerg",
    alert: "logs.level.alert",
    crit: "logs.level.crit",
    err: "logs.level.err",
    warning: "logs.level.warning",
    notice: "logs.level.notice",
    info: "logs.level.info",
    debug: "logs.level.debug"
  };

  // The server gives UTC times with their offset; the browser shows them in its own time zone,
  // in the language of the page, as toLocaleString writes them. One formatter per language for
  // every row (reactive, so that the list follows a change of the language).
  let dateFormat = $derived(
    new Intl.DateTimeFormat($intlLocale, {
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
      second: "numeric"
    })
  );
  function formatTimestamp(ts: string): string {
    if (!ts) return "";
    const date = apiTime(ts);
    return date ? dateFormat.format(date) : ts;
  }
</script>

<!-- Error Alert, instead of the list: no entries belong to the request that failed -->
{#if error}
  <Alert variant="error" title={$t("logs.loadError")}>
    {error}
  </Alert>
{:else}
  <!-- Logs Display -->
  <Card>
    <div class="flex flex-wrap items-center justify-between gap-2 mb-3">
      <span class="text-sm text-secondary">
        {#if loading}
          {$t("common.loading")}
        {:else}
          {$t("logs.count", { n: entries.length })}
        {/if}
      </span>
      {#if autoRefresh}
        <span class="text-xs text-success flex items-center gap-1">
          <span class="w-2 h-2 bg-success rounded-full animate-pulse"></span>
          {$t("logs.following")}
        </span>
      {:else if autoStopped}
        <span class="text-xs text-warning">
          {$t(autoStopped === "forbidden" ? "logs.autoForbidden" : "logs.autoLost")}
        </span>
      {/if}
    </div>

    <div class="bg-surface-muted rounded-lg overflow-hidden">
      <div class="max-h-[600px] overflow-y-auto scrollbar-thin">
        {#if loading && entries.length === 0}
          <div class="flex items-center justify-center py-12">
            <RefreshCw class="w-6 h-6 animate-spin text-secondary" />
            <span class="ml-2 text-secondary">{$t("logs.loadingEntries")}</span>
          </div>
        {:else if entries.length === 0}
          <EmptyState
            icon={FileText}
            title={$t("logs.empty")}
            description={hasActiveFilters ? $t("logs.emptyFiltered") : $t("logs.emptySource")}
          />
        {:else}
          <!-- Below sm each entry stacks: its time, level and program on a line, the message
               below them at the full width (ADR-0302). -->
          <table class="w-full text-sm max-sm:block">
            <thead class="sticky top-0 bg-surface-overlay border-b border-border max-sm:hidden">
              <tr>
                <th class="px-3 py-2 text-left text-secondary font-medium w-[160px]">{$t("logs.column.time")}</th>
                <th class="px-3 py-2 text-left text-secondary font-medium w-[80px]">{$t("logs.column.level")}</th>
                <th class="px-3 py-2 text-left text-secondary font-medium w-[120px]">{$t("logs.column.source")}</th>
                <th class="px-3 py-2 text-left text-secondary font-medium">{$t("logs.column.message")}</th>
              </tr>
            </thead>
            <tbody class="font-mono text-xs max-sm:block">
              {#each entries as entry, i}
                <tr
                  class="border-b border-border/50 hover:bg-surface-overlay/50 transition-colors max-sm:flex max-sm:flex-wrap max-sm:items-center max-sm:gap-x-3 max-sm:px-3 max-sm:py-2"
                >
                  <td class="px-3 py-2 text-secondary whitespace-nowrap max-sm:p-0">
                    {formatTimestamp(entry.timestamp)}
                  </td>
                  <td class="px-3 py-2 max-sm:p-0">
                    <Badge variant={priorityVariant(entry.priority)} class="text-xs">
                      {textOf($t, LEVELS, entry.priority)}
                    </Badge>
                  </td>
                  <td class="px-3 py-2 text-secondary max-sm:p-0 max-sm:min-w-0">
                    {#if entry.unit}
                      <span class="truncate block max-w-[120px]" title={entry.unit}>
                        {entry.unit}
                      </span>
                    {:else}
                      <span class="text-muted">-</span>
                    {/if}
                  </td>
                  <td class="px-3 py-2 text-content max-sm:w-full max-sm:p-0 max-sm:pt-1">
                    <span class="break-all"
                      >{#each splitByMatch(entry.message ?? "", search) as part}{#if part.match}<mark
                            class="bg-status-warning/20 text-status-warning rounded-sm">{part.text}</mark
                          >{:else}{part.text}{/if}{/each}</span
                    >
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>
    </div>
  </Card>
{/if}
