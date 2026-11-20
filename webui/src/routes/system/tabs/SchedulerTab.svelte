<script lang="ts">
  import { onMount } from "svelte";
  import { getTasks, deleteTask, runTaskNow, toggleTask, type ScheduledTask } from "$lib/api/scheduler";
  import { ApiError } from "$lib/api/client";
  import { getPools, getDisks, type Pool, type Disk } from "$lib/api/storage";
  import { Button, EmptyState, HelpText, LoadState, Skeleton, confirm, toast } from "$lib/components/ui";
  import { TaskFormDialog, TaskHistory, TaskHistoryDialog, TaskRow } from "$lib/components/system/scheduler";
  import { messageOf } from "$lib/errors";
  import { t } from "$lib/i18n";
  import { nasTimeZone } from "$lib/stores/nasTime";
  import { Calendar, RefreshCw, Plus, History } from "lucide-svelte";

  // The tasks of the Scheduler, on the NAS's clock (ADR-0432): the tab names the zone once, and
  // every time it shows is the NAS's. A failed load says so in place of the list, never "no tasks";
  // what an action did or why it failed comes as a toast.
  let tasks = $state<ScheduledTask[]>([]);
  let pools = $state<Pool[]>([]);
  let disks = $state<Disk[]>([]);
  let disksLoaded = $state(false);
  let loading = $state(true);
  // The first load has answered: a reload keeps the list (ADR-0430).
  let loaded = $state(false);
  let loadError = $state("");
  let runningTaskId = $state<string | null>(null);

  // Modal state: the task dialog, with the task being edited (null for a new one), and the history.
  let showTaskModal = $state(false);
  let editingTask = $state<ScheduledTask | null>(null);
  const history = new TaskHistory();

  onMount(async () => {
    await Promise.all([loadTasks(), loadResources()]);
  });

  async function loadTasks() {
    loading = true;
    try {
      const result = await getTasks();
      tasks = Array.isArray(result) ? result : [];
      loadError = "";
    } catch (e) {
      // A 401 has already ended the session.
      if (!(e instanceof ApiError && e.status === 401)) loadError = messageOf(e);
    }
    loading = false;
    loaded = true;
  }

  async function loadResources() {
    try {
      const [poolsResult, disksResult] = await Promise.all([getPools(), getDisks()]);
      pools = poolsResult;
      disks = disksResult.filter((d) => d.type === "disk");
      disksLoaded = true;
    } catch (e) {
      console.error("Failed to load resources:", e);
    }
  }

  function openCreateModal() {
    editingTask = null;
    showTaskModal = true;
  }

  function openEditModal(task: ScheduledTask) {
    editingTask = task;
    showTaskModal = true;
  }

  function closeTaskModal() {
    showTaskModal = false;
    editingTask = null;
  }

  async function taskSaved() {
    closeTaskModal();
    await loadTasks();
  }

  /** A failed action as a toast: the action's title and the server's reason. */
  function failed(titleKey: string, e: unknown) {
    if (e instanceof ApiError && e.status === 401) return;
    toast.error(messageOf(e), { title: $t(titleKey) });
  }

  async function handleDeleteTask(task: ScheduledTask) {
    if (!task.id) return;
    const yes = await confirm({
      title: $t("scheduler.task.confirmDelete", { name: task.name }),
      message: $t("scheduler.task.deleteMessage"),
      confirmLabel: $t("scheduler.task.deleteAction"),
    });
    if (!yes) return;

    try {
      await deleteTask(task.id);
      toast.success($t("scheduler.task.deleted", { name: task.name }));
      await loadTasks();
    } catch (e) {
      failed("scheduler.task.deleteFailed", e);
    }
  }

  async function handleToggleTask(task: ScheduledTask) {
    if (!task.id) return;

    try {
      const { enabled } = await toggleTask(task.id);
      toast.success($t(enabled ? "scheduler.task.enabledNow" : "scheduler.task.disabledNow", { name: task.name }));
      await loadTasks();
    } catch (e) {
      failed("scheduler.task.toggleFailed", e);
      // The switch shows the stored state again.
      await loadTasks();
    }
  }

  async function handleRunNow(task: ScheduledTask) {
    if (!task.id) return;

    runningTaskId = task.id;
    try {
      await runTaskNow(task.id);
      toast.success($t("scheduler.task.started", { name: task.name }), {
        action: { label: $t("scheduler.task.viewHistory"), run: () => history.show(task.id, task.name) },
      });
      await loadTasks();
    } catch (e) {
      failed("scheduler.task.runFailed", e);
    }
    runningTaskId = null;
  }
</script>

<div class="space-y-4">
  <!-- Header -->
  <div class="flex flex-wrap items-center justify-between gap-3">
    <h2 class="text-xl font-semibold">{$t("scheduler.title")}</h2>
    <div class="flex flex-wrap gap-2">
      <Button variant="secondary" size="sm" icon={History} onclick={() => history.show()}>
        {$t("scheduler.history")}
      </Button>
      <Button variant="secondary" size="icon" onclick={loadTasks} title={$t("common.refresh")} aria-label={$t("common.refresh")}>
        <RefreshCw class="w-4 h-4 {loading && loaded ? 'animate-spin' : ''}" />
      </Button>
      <Button variant="primary" size="sm" icon={Plus} onclick={openCreateModal}>
        {$t("scheduler.newTask")}
      </Button>
    </div>
  </div>
  <!-- The zone of every time of the tab, once; its line keeps its height until it is known. -->
  <div class="min-h-[18px]">
    {#if $nasTimeZone}
      <HelpText text={$t("scheduler.timeZone", { zone: $nasTimeZone })}>
        {#snippet more()}<p>{$t("scheduler.timeZoneMore")}</p>{/snippet}
      </HelpText>
    {/if}
  </div>

  <!-- Task List -->
  <section class="card" aria-label={$t("scheduler.tasksLabel")}>
    <LoadState
      loading={!loaded}
      error={loadError}
      errorTitle={$t("scheduler.loadFailed")}
      keepOnError={tasks.length > 0}
      empty={tasks.length === 0}
      onretry={loadTasks}
      retrying={loading && loaded}
    >
      {#snippet skeleton()}<Skeleton count={3} size="control" />{/snippet}
      {#snippet emptyState()}
        <EmptyState icon={Calendar} compact title={$t("scheduler.empty")} description={$t("scheduler.emptyHint")} />
      {/snippet}
      <div class="space-y-2">
        {#each tasks as task (task.id)}
          <TaskRow
            {task}
            zone={$nasTimeZone}
            running={runningTaskId === task.id}
            ontoggle={handleToggleTask}
            onrun={handleRunNow}
            onhistory={(task) => history.show(task.id, task.name)}
            onedit={openEditModal}
            ondelete={handleDeleteTask}
          />
        {/each}
      </div>
    </LoadState>
  </section>
</div>

<!-- Create/Edit Task Modal -->
{#if showTaskModal}
  <TaskFormDialog task={editingTask} {pools} {disks} {disksLoaded} timeZone={$nasTimeZone ?? ""} onclose={closeTaskModal} onsaved={taskSaved} />
{/if}

<!-- History of the runs -->
<TaskHistoryDialog {history} zone={$nasTimeZone} />
