<script lang="ts">
  // The state of the off-device copy (ADR-0337): what runs, the last copy and the last restore,
  // with Refresh and Run now, and the task of the Scheduler that runs the copy.
  import { formatBytes } from "$lib/api/backup";
  import type { OffsiteStatus } from "$lib/api/offsite";
  import type { ScheduledTask } from "$lib/api/scheduler";
  import { Button } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { formatDateTime } from "$lib/time";
  import { Play, RefreshCw } from "lucide-svelte";

  type Props = {
    status: OffsiteStatus | null;
    // The copy's tasks of the Scheduler.
    tasks: ScheduledTask[];
    // The units of sizes in the language of the page (formatBytes).
    sizeUnits: string[];
    starting: boolean;
    onrefresh: () => void;
    onrunnow: () => void;
  };

  let { status, tasks, sizeUnits, starting, onrefresh, onrunnow }: Props = $props();

  // The step at which a copy failed, as the server names it, with the keys of their texts (ADR-0287).
  const STEPS: Record<string, string> = {
    settings: "offsite.step.settings",
    folders: "offsite.step.folders",
    unlock: "offsite.step.unlock",
    configuration: "offsite.step.configuration",
    data: "offsite.step.data",
    forget: "offsite.step.forget",
    check: "offsite.step.check",
    restore_test: "offsite.step.restore_test",
  };

  let running = $derived(status?.running ?? null);
  let lastCopy = $derived(status?.last_copy ?? null);
  let lastRestore = $derived(status?.last_restore ?? null);

  function when(iso: string | null | undefined): string {
    return formatDateTime(iso, $intlLocale);
  }

  function stepText(step: string | null): string {
    return step && Object.prototype.hasOwnProperty.call(STEPS, step) ? $t(STEPS[step]) : (step ?? "");
  }

  function runningText(activity: NonNullable<OffsiteStatus["running"]>): string {
    const time = when(activity.started);
    if (activity.kind === "copy") return $t("offsite.running.copy", { time });
    if (activity.kind === "restore-files") return $t("offsite.running.restoreFiles", { folder: activity.folder ?? "", time });
    if (activity.kind === "restore-configuration") return $t("offsite.running.restoreConfiguration", { time });
    return $t("offsite.running.settings");
  }
</script>

<div class="card-nested p-3 mb-4 space-y-2 text-sm">
  <div class="flex flex-wrap items-center justify-between gap-2">
    <h4 class="font-medium">{$t("offsite.state.title")}</h4>
    <div class="flex flex-wrap gap-2">
      <Button size="sm" variant="secondary" icon={RefreshCw} onclick={onrefresh}>{$t("offsite.refresh")}</Button>
      <Button size="sm" variant="secondary" icon={Play} loading={starting} disabled={running !== null} onclick={onrunnow}>
        {$t("offsite.state.runNow")}
      </Button>
    </div>
  </div>
  {#if running}
    <p class="text-status-info">{runningText(running)}</p>
  {/if}
  {#if lastCopy}
    <p class="flex flex-wrap items-center gap-2">
      <span class={lastCopy.success ? "text-status-success" : "text-status-error"}>
        {lastCopy.success ? $t("offsite.state.succeeded") : $t("offsite.state.failed")}
      </span>
      <span class="text-content-muted">
        {$t("offsite.state.started", { time: when(lastCopy.started) })},
        {lastCopy.trigger === "task" ? $t("offsite.state.byTask") : $t("offsite.state.byHand")}
      </span>
    </p>
    {#if lastCopy.summary}
      <p>
        {$t("offsite.state.files", {
          new: lastCopy.summary.files_new,
          changed: lastCopy.summary.files_changed,
          unmodified: lastCopy.summary.files_unmodified,
          added: formatBytes(lastCopy.summary.bytes_added, $intlLocale, sizeUnits),
        })}
      </p>
    {/if}
    <p class="flex flex-wrap gap-x-4">
      <span>
        {lastCopy.check === "passed"
          ? $t("offsite.state.checkPassed")
          : lastCopy.check === "failed"
            ? $t("offsite.state.checkFailed")
            : $t("offsite.state.checkNotRun")}
      </span>
      <span>
        {lastCopy.restore_test === "passed"
          ? $t("offsite.state.restoreTestPassed")
          : lastCopy.restore_test === "failed"
            ? $t("offsite.state.restoreTestFailed")
            : $t("offsite.state.restoreTestNotRun")}
      </span>
    </p>
    {#if lastCopy.error}
      <!-- A plain sentence, and restic's own words on demand. -->
      <div data-testid="copy-error">
        <p class="text-status-error break-words">{$t("offsite.state.failedAt", { step: stepText(lastCopy.step) })}</p>
        <details class="text-content-secondary">
          <summary class="link w-fit cursor-pointer text-xs">{$t("offsite.details")}</summary>
          <p class="mt-1 font-mono text-xs break-words select-all">{lastCopy.error}</p>
        </details>
      </div>
    {/if}
    {#if lastCopy.note}
      <p class="text-status-warning break-words" data-testid="copy-note">{lastCopy.note}</p>
    {/if}
  {:else}
    <p>{$t("offsite.state.never")}</p>
  {/if}
  {#if lastRestore}
    <p class="break-all">
      <span class={lastRestore.success ? "text-status-success" : "text-status-error"}>
        {lastRestore.success ? $t("offsite.restore.succeeded") : $t("offsite.restore.failed")}
      </span>
      <span class="font-mono text-xs" data-testid="restore-folder">{lastRestore.folder}</span>
    </p>
    {#if lastRestore.error}
      <p class="text-status-error break-words">{lastRestore.error}</p>
    {/if}
  {/if}

  <!-- The task of the Scheduler that runs the copy: a task is made in the Scheduler, its one
       place (ADR-0431). -->
  {#if tasks.length === 0}
    <p class="pt-1 break-words">
      <span class="text-status-warning">{$t("offsite.task.none")}</span>
      <a href="#scheduler" class="link">{$t("offsite.task.schedule")}</a>
    </p>
  {:else}
    <p class="pt-1 break-words">
      {$t("offsite.task.some", { names: tasks.map((task) => task.name).join(", ") })}
      {#if tasks.find((task) => task.enabled && task.next_run)}
        {$t("offsite.task.next", { time: when(tasks.find((task) => task.enabled && task.next_run)?.next_run) })}
      {/if}
    </p>
  {/if}
</div>
