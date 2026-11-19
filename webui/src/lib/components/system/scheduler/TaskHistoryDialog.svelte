<script lang="ts">
  import { CheckCircle, History, RefreshCw, X, XCircle } from "lucide-svelte";
  import { Button } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { apiTime, formatDateTime } from "$lib/time";
  import type { TaskHistory } from "./history.svelte";

  // The runs of every task, or of one, newest first: drawn while the history is open.
  type Props = {
    history: TaskHistory;
  };

  let { history }: Props = $props();

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

<div class="modal-overlay p-4">
  <div class="bg-surface-elevated rounded-lg w-full max-w-2xl max-h-[80vh] flex flex-col">
    <!-- Modal Header -->
    <div class="flex items-center justify-between p-4 border-b border-border">
      <h3 class="text-lg font-bold">
        {$t("scheduler.runs.title", {
          name: history.taskId ? history.taskName || $t("scheduler.runs.task") : $t("scheduler.runs.allTasks"),
        })}
      </h3>
      <Button variant="ghost" size="icon-sm" onclick={() => history.close()}>
        <X class="w-5 h-5" />
      </Button>
    </div>

    <!-- Modal Body -->
    <div class="flex-1 overflow-y-auto p-4">
      {#if history.runs.length === 0}
        <div class="text-center py-8 text-content-secondary">
          <History class="w-10 h-10 mx-auto mb-2 opacity-50" />
          <p>{$t("scheduler.runs.empty")}</p>
        </div>
      {:else}
        <div class="space-y-2">
          {#each history.runs as run}
            <div class="p-3 bg-surface-overlay rounded-lg">
              <div class="flex items-center justify-between mb-2">
                <div class="flex items-center gap-2">
                  {#if run.status === "success"}
                    <CheckCircle class="w-4 h-4 text-status-success" />
                  {:else if run.status === "failed"}
                    <XCircle class="w-4 h-4 text-status-error" />
                  {:else}
                    <RefreshCw class="w-4 h-4 text-status-warning animate-spin" />
                  {/if}
                  <span class="font-medium text-sm">{run.task_name}</span>
                  <span class="text-xs px-2 py-0.5 rounded {run.status === 'success'
                    ? 'bg-status-success/20 text-status-success'
                    : run.status === 'failed'
                      ? 'bg-status-error/20 text-status-error'
                      : 'bg-status-warning/20 text-status-warning'}">
                    {getStatusLabel(run.status)}
                  </span>
                </div>
                <span class="text-xs text-content-muted">{formatDuration(run.started_at, run.finished_at)}</span>
              </div>
              <div class="flex flex-wrap gap-x-3 text-xs text-content-secondary">
                <span>{formatDateTime(run.started_at, $intlLocale)}</span>
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
                  <div class="mt-1 p-2 bg-surface rounded text-xs text-content-soft font-mono whitespace-pre-wrap break-all max-h-32 overflow-y-auto">
                    {run.output}
                  </div>
                </details>
              {/if}
            </div>
          {/each}
        </div>
      {/if}
    </div>

    <!-- Modal Footer -->
    <div class="p-4 border-t border-border">
      <Button variant="secondary" class="w-full" onclick={() => history.close()}>
        {$t("scheduler.runs.close")}
      </Button>
    </div>
  </div>
</div>
