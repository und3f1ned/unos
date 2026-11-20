<script lang="ts">
  import { Edit3, History, Play, Trash2 } from "lucide-svelte";
  import type { ScheduledTask, TaskType } from "$lib/api/scheduler";
  import { Badge, Button } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { formatDateTime } from "$lib/time";
  import { DAYS_OF_WEEK, TASK_TYPES, getTaskTypeIcon } from "./tasks";

  // A task of the list: its switch, type, name, target, schedule and next run, and its actions.
  type Props = {
    task: ScheduledTask;
    // Run Now was asked and the server has not answered yet.
    running: boolean;
    // The on/off was changed and the server has not answered yet: the box shows the new state.
    toggling: boolean;
    ontoggle: (task: ScheduledTask) => void;
    onrun: (task: ScheduledTask) => void;
    onhistory: (task: ScheduledTask) => void;
    onedit: (task: ScheduledTask) => void;
    ondelete: (task: ScheduledTask) => void;
    // The NAS's time zone, whose clock the schedules and the next run are on; undefined while unknown.
    timeZone: string | undefined;
  };

  let { task, running, toggling, ontoggle, onrun, onhistory, onedit, ondelete, timeZone }: Props = $props();

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

<!-- Below sm the actions wrap below the task, which keeps the row's width (ADR-0302). The on/off
     is a checkbox, the place of the Switch of the design system: an action icon no longer doubles as
     the state. Action icons are neutral, each with its name as a tooltip. -->
<div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 py-3">
  <div class="flex min-w-0 flex-1 items-start gap-3 max-sm:basis-full">
    <input
      type="checkbox"
      class="form-checkbox mt-1 h-4 w-4 shrink-0"
      checked={task.enabled}
      disabled={toggling}
      title={task.enabled ? $t("scheduler.task.disable") : $t("scheduler.task.enable")}
      aria-label={$t("scheduler.task.onOff", { name: task.name })}
      onchange={() => ontoggle(task)}
    />

    <div class="min-w-0 flex-1">
      <div class="flex flex-wrap items-center gap-2">
        <TypeIcon class="h-4 w-4 shrink-0 text-content-secondary" aria-hidden="true" />
        <p class="min-w-0 truncate text-sm font-medium" title={task.name}>{task.name}</p>
        {#if !task.enabled}
          <Badge variant="neutral">{$t("scheduler.task.paused")}</Badge>
        {:else if task.last_status === "failed"}
          <Badge variant="error" dot>{$t("scheduler.task.lastFailed")}</Badge>
        {/if}
      </div>
      <p class="truncate text-xs text-content-secondary">
        {getTaskTypeLabel(task.task_type)}
        {#if task.target_pool}
          {$t("scheduler.task.on", { target: task.target_pool })}
        {:else if task.target_disk}
          {$t("scheduler.task.on", { target: task.target_disk })}
        {/if}
        &bull; {formatSchedule(task)}
      </p>
      {#if task.enabled && task.next_run}
        <p class="mt-0.5 text-xs text-content-muted tabular">
          <span>{$t("scheduler.task.nextRun")}</span>
          <span>{formatDateTime(task.next_run, $intlLocale, { dateStyle: "medium", timeStyle: "short", timeZone })}</span>
        </p>
      {/if}
    </div>
  </div>

  <div class="ml-auto flex shrink-0 items-center gap-1">
    <Button variant="ghost" size="icon-sm" icon={Play} loading={running}
      onclick={() => onrun(task)}
      title={$t("scheduler.task.runNow")}
    >
      <span class="sr-only">{$t("scheduler.task.runNow")}</span>
    </Button>
    <Button variant="ghost" size="icon-sm" icon={History}
      onclick={() => onhistory(task)}
      title={$t("scheduler.task.viewHistory")}
    >
      <span class="sr-only">{$t("scheduler.task.viewHistory")}</span>
    </Button>
    <Button variant="ghost" size="icon-sm" icon={Edit3}
      onclick={() => onedit(task)}
      title={$t("scheduler.task.edit")}
    >
      <span class="sr-only">{$t("scheduler.task.edit")}</span>
    </Button>
    <Button variant="ghost-danger" size="icon-sm" icon={Trash2}
      onclick={() => ondelete(task)}
      title={$t("scheduler.task.delete")}
    >
      <span class="sr-only">{$t("scheduler.task.delete")}</span>
    </Button>
  </div>
</div>
