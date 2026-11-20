<script lang="ts">
  import { CheckCircle, History, RefreshCw, XCircle } from "lucide-svelte";
  import { Badge, Button, EmptyState, Sheet } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { apiTime, formatDateTime } from "$lib/time";
  import type { TaskHistory } from "./history.svelte";

  // The runs of every task, or of one, newest first, in a side panel (a Sheet, ADR-0432), their
  // times on the NAS's clock.
  type Props = {
    history: TaskHistory;
    // The NAS's time zone; null while it is not known (the browser's then).
    zone: string | null;
  };

  let { history, zone }: Props = $props();

  const STATUS_VARIANTS: Record<string, "success" | "error" | "warning"> = {
    success: "success",
    failed: "error",
    running: "warning",
  };

  // The status of a run (TaskRun.status), by value.
  const RUN_STATUSES: Record<string, string> = {
    success: "scheduler.runs.success",
    failed: "scheduler.runs.failed",
    running: "scheduler.runs.running",
  };

  // The functions below that give texts are reactive, so that the history follows a change of
  // the language.

  // A status without a label as it comes.
  let getStatusLabel = $derived((status: string): string =>
    Object.prototype.hasOwnProperty.call(RUN_STATUSES, status) ? $t(RUN_STATUSES[status]) : status);

  let formatDuration = $derived((start: string, end?: string): string => {
    if (!end) return $t("scheduler.runs.inProgress");
    const ms = (apiTime(end)?.getTime() ?? NaN) - (apiTime(start)?.getTime() ?? NaN);
    if (Number.isNaN(ms)) return "";
    if (ms < 1000) return $t("scheduler.runs.milliseconds", { ms });
    if (ms < 60000) {
      // One decimal, with the decimal separator of the page's language.
      const seconds = (ms / 1000).toLocaleString($intlLocale, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
      return $t("scheduler.runs.seconds", { seconds });
    }
    return $t("scheduler.runs.minutes", { minutes: Math.floor(ms / 60000), seconds: Math.floor((ms % 60000) / 1000) });
  });
</script>

<Sheet
  open={history.open}
  title={$t("scheduler.runs.title", {
    name: history.taskId ? history.taskName || $t("scheduler.runs.task") : $t("scheduler.runs.allTasks"),
  })}
  size="lg"
  onclose={() => history.close()}
>
  {#if history.runs.length === 0}
    <EmptyState icon={History} compact title={$t("scheduler.runs.empty")} />
  {:else}
    <ul class="space-y-2">
      {#each history.runs as run (run.id)}
        <li class="p-3 bg-surface-overlay rounded-lg">
          <div class="flex flex-wrap items-center justify-between gap-2 mb-2">
            <div class="flex items-center gap-2 min-w-0">
              {#if run.status === "success"}
                <CheckCircle class="w-4 h-4 shrink-0 text-status-success" aria-hidden="true" />
              {:else if run.status === "failed"}
                <XCircle class="w-4 h-4 shrink-0 text-status-error" aria-hidden="true" />
              {:else}
                <RefreshCw class="w-4 h-4 shrink-0 text-status-warning animate-spin" aria-hidden="true" />
              {/if}
              <span class="font-medium text-sm break-words min-w-0">{run.task_name}</span>
              <Badge variant={STATUS_VARIANTS[run.status] ?? "neutral"}>{getStatusLabel(run.status)}</Badge>
            </div>
            <span class="text-xs text-content-muted tabular">{formatDuration(run.started_at, run.finished_at)}</span>
          </div>
          <div class="flex flex-wrap gap-x-3 text-xs text-content-secondary">
            <span class="tabular">{formatDateTime(run.started_at, $intlLocale, { zone })}</span>
            {#if typeof run.exit_code === "number"}
              <span>{$t("scheduler.runs.exitStatus", { code: run.exit_code })}</span>
            {/if}
          </div>
          {#if run.error}
            <div class="mt-2 p-2 bg-status-error/10 rounded text-xs text-status-error font-mono break-all">
              {run.error}
            </div>
          {/if}
          {#if run.output}
            <details class="mt-2">
              <summary class="text-xs text-content-muted cursor-pointer hover:text-content-secondary">{$t("scheduler.runs.output")}</summary>
              <div class="mt-1 p-2 bg-surface rounded text-xs text-content-soft font-mono whitespace-pre-wrap break-all">
                {run.output}
              </div>
            </details>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
  {#snippet footer()}
    <Button variant="secondary" onclick={() => history.close()}>{$t("scheduler.runs.close")}</Button>
  {/snippet}
</Sheet>
