<script lang="ts">
  import { untrack } from "svelte";
  import { CheckCircle, Clock, Edit3, History, Trash2, XCircle, Zap } from "lucide-svelte";
  import type { ScheduledTask, TaskType } from "$lib/api/scheduler";
  import { ActionMenu, Button, Switch } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { formatDateTime } from "$lib/time";
  import { DAYS_OF_WEEK, TASK_TYPES, getStatusColor, getTaskTypeIcon } from "./tasks";

  // A task of the list (ADR-0432): its switch, type, name, target, schedule and next run on the
  // NAS's clock; Edit and Delete as icons, Run now and its history in its ⋯ menu.
  type Props = {
    task: ScheduledTask;
    // The NAS's time zone, whose clock the times are on; null while it is not known.
    zone: string | null;
    // Run Now was asked and the server has not answered yet.
    running: boolean;
    ontoggle: (task: ScheduledTask) => void;
    onrun: (task: ScheduledTask) => void;
    onhistory: (task: ScheduledTask) => void;
    onedit: (task: ScheduledTask) => void;
    ondelete: (task: ScheduledTask) => void;
  };

  let { task, zone, running, ontoggle, onrun, onhistory, onedit, ondelete }: Props = $props();

  let TypeIcon = $derived(getTaskTypeIcon(task.task_type));

  // The switch's state: the task's, again after each load (a new task object), so that a toggle
  // that failed shows the stored state once the list is read again.
  let on = $state(untrack(() => task.enabled));
  $effect.pre(() => {
    const current = task;
    untrack(() => (on = current.enabled));
  });

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

  const nextRun = $derived(
    task.enabled && task.next_run
      ? formatDateTime(task.next_run, $intlLocale, { dateStyle: "medium", timeStyle: "short", zone })
      : ""
  );
</script>

<!-- Below sm the actions wrap below the task, which keeps the row's width (ADR-0302). -->
<div class="flex flex-wrap gap-y-2 items-center justify-between p-3 bg-surface-overlay rounded-lg" data-task={task.name}>
  <div class="flex items-center gap-3 flex-1 max-sm:basis-full min-w-0">
    <!-- On or off: a switch, which is no action icon (ADR-0432). -->
    <Switch
      bind:checked={on}
      label={$t("scheduler.task.enabledLabel", { name: task.name })}
      onchange={() => ontoggle(task)}
    />

    <!-- Task Info -->
    <div class="flex-1 min-w-0">
      <div class="flex items-center gap-2">
        <TypeIcon class="w-4 h-4 text-content-secondary flex-shrink-0" aria-hidden="true" />
        <p class="font-medium text-sm truncate" title={task.name}>{task.name}</p>
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

    <!-- Next Run, on the NAS's clock -->
    {#if nextRun}
      <div class="hidden sm:flex flex-col items-end text-xs text-content-muted">
        <span class="flex items-center gap-1">
          <Clock class="w-3 h-3" />
          {$t("scheduler.task.nextRun")}
        </span>
        <span class="tabular">{nextRun}</span>
      </div>
    {/if}
  </div>

  <!-- Actions: one line, the two icons and the menu. -->
  <div class="table-actions ml-2 max-sm:ml-auto">
    <Button variant="ghost" size="icon-sm" icon={Edit3} onclick={() => onedit(task)} title={$t("scheduler.task.edit")} aria-label={$t("scheduler.task.editOne", { name: task.name })}></Button>
    <Button variant="ghost-danger" size="icon-sm" icon={Trash2} onclick={() => ondelete(task)} title={$t("scheduler.task.delete")} aria-label={$t("scheduler.task.deleteOne", { name: task.name })}></Button>
    <ActionMenu
      name={task.name}
      items={[
        { label: $t("scheduler.task.runNow"), icon: Zap, disabled: running, onclick: () => onrun(task) },
        { label: $t("scheduler.task.viewHistory"), icon: History, onclick: () => onhistory(task) },
      ]}
    />
  </div>
</div>
