<script lang="ts">
  import { CheckCircle, Clock, Edit3, History, Pause, Play, RefreshCw, Trash2, XCircle, Zap } from "lucide-svelte";
  import type { ScheduledTask, TaskType } from "$lib/api/scheduler";
  import { Button } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { formatDateTime } from "$lib/time";
  import { DAYS_OF_WEEK, TASK_TYPES, getStatusColor, getTaskTypeIcon } from "./tasks";

  // A task of the list: its switch, type, name, target, schedule and next run, and its actions.
  type Props = {
    task: ScheduledTask;
    // Run Now was asked and the server has not answered yet.
    running: boolean;
    ontoggle: (task: ScheduledTask) => void;
    onrun: (task: ScheduledTask) => void;
    onhistory: (task: ScheduledTask) => void;
    onedit: (task: ScheduledTask) => void;
    ondelete: (task: ScheduledTask) => void;
  };

  let { task, running, ontoggle, onrun, onhistory, onedit, ondelete }: Props = $props();

  let TypeIcon = $derived(getTaskTypeIcon(task.task_type));

  // The functions below that give texts are reactive, so that the list follows a change of the
  // language.

  // A type without a label as it comes.
  let getTaskTypeLabel = $derived((type: TaskType): string => {
    const known = TASK_TYPES.find((option) => option.value === type);
    return known ? $t(known.key) : type;
  });

  // On the clock of the NAS's time zone. A weekly or monthly task without its day runs on the
  // server's default, Sunday or the 1st (get_cron_expression of scheduler/router.py).
  let formatSchedule = $derived((task: ScheduledTask): string => {
    if (task.frequency === "custom" && task.cron_expression) {
      return task.cron_expression;
    }

    const minute = String(task.minute).padStart(2, "0");
    const time = `${String(task.hour).padStart(2, "0")}:${minute}`;
    const weekday = DAYS_OF_WEEK.find((d) => d.value === (task.day_of_week ?? 0));

    if (task.frequency === "hourly") return $t("scheduler.schedule.hourly", { minute });
    if (task.frequency === "daily") return $t("scheduler.schedule.daily", { time });
    if (task.frequency === "weekly" && weekday) return $t(weekday.schedule, { time });
    if (task.frequency === "monthly") return $t("scheduler.schedule.monthly", { day: task.day_of_month ?? 1, time });
    return task.frequency;
  });
</script>

<!-- Below sm the actions wrap below the task, which keeps the row's width (ADR-0302). -->
<div class="flex flex-wrap gap-y-2 items-center justify-between p-3 bg-surface-overlay rounded-lg">
  <div class="flex items-center gap-3 flex-1 max-sm:basis-full min-w-0">
    <!-- Enable/Disable Toggle -->
    <Button variant="plain"
      onclick={() => ontoggle(task)}
      class="p-1.5 rounded hover:bg-surface-hover transition-colors {task.enabled
        ? 'text-status-success'
        : 'text-content-muted'}"
      title={task.enabled ? $t("scheduler.task.disable") : $t("scheduler.task.enable")}
    >
      {#if task.enabled}
        <Play class="w-5 h-5" />
      {:else}
        <Pause class="w-5 h-5" />
      {/if}
    </Button>

    <!-- Task Info -->
    <div class="flex-1 min-w-0">
      <div class="flex items-center gap-2">
        <TypeIcon class="w-4 h-4 text-status-info flex-shrink-0" />
        <p class="font-medium text-sm truncate">{task.name}</p>
        {#if task.last_status}
          <span class={getStatusColor(task.last_status)}>
            {#if task.last_status === "success"}
              <CheckCircle class="w-3.5 h-3.5" />
            {:else if task.last_status === "failed"}
              <XCircle class="w-3.5 h-3.5" />
            {/if}
          </span>
        {/if}
      </div>
      <p class="text-xs text-content-secondary truncate">
        {getTaskTypeLabel(task.task_type)}
        {#if task.target_pool}
          {$t("scheduler.task.on", { target: task.target_pool })}
        {:else if task.target_disk}
          {$t("scheduler.task.on", { target: task.target_disk })}
        {/if}
        &bull; {formatSchedule(task)}
      </p>
    </div>

    <!-- Next Run -->
    {#if task.enabled && task.next_run}
      <div class="hidden sm:flex flex-col items-end text-xs text-content-muted">
        <span class="flex items-center gap-1">
          <Clock class="w-3 h-3" />
          {$t("scheduler.task.nextRun")}
        </span>
        <span>{formatDateTime(task.next_run, $intlLocale)}</span>
      </div>
    {/if}
  </div>

  <!-- Actions -->
  <div class="flex items-center gap-1 ml-2 max-sm:ml-auto">
    <Button variant="ghost" size="icon" class="text-status-warning hover:text-status-warning"
      onclick={() => onrun(task)}
      disabled={running}
      title={$t("scheduler.task.runNow")}
    >
      {#if running}
        <RefreshCw class="w-4 h-4 animate-spin" />
      {:else}
        <Zap class="w-4 h-4" />
      {/if}
    </Button>
    <Button variant="ghost" size="icon" class="text-content-secondary"
      onclick={() => onhistory(task)}
      title={$t("scheduler.task.viewHistory")}
    >
      <History class="w-4 h-4" />
    </Button>
    <Button variant="ghost" size="icon"
      onclick={() => onedit(task)}
      title={$t("scheduler.task.edit")}
    >
      <Edit3 class="w-4 h-4" />
    </Button>
    <Button variant="ghost-danger" size="icon"
      onclick={() => ondelete(task)}
      title={$t("scheduler.task.delete")}
    >
      <Trash2 class="w-4 h-4" />
    </Button>
  </div>
</div>
