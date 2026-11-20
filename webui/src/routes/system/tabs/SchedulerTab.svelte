<script lang="ts">
  import { onMount } from "svelte";
  import {
    getTasks,
    deleteTask,
    runTaskNow,
    toggleTask,
    getScheduleTimezone,
    type ScheduledTask,
  } from "$lib/api/scheduler";
  import { getPools, getDisks, type Pool, type Disk } from "$lib/api/storage";
  import { Button, Skeleton } from "$lib/components/ui";
  import { TaskFormDialog, TaskHistory, TaskHistoryDialog, TaskRow } from "$lib/components/system/scheduler";
  import { t } from "$lib/i18n";
  import { Calendar, RefreshCw, Plus, History } from "lucide-svelte";

  // State
  let tasks = $state<ScheduledTask[]>([]);
  let pools = $state<Pool[]>([]);
  let disks = $state<Disk[]>([]);
  let disksLoaded = $state(false);
  // The time zone whose clock the schedules follow (the NAS's); empty until it is known.
  let timeZone = $state("");
  let loading = $state(true);
  // The first load has answered: a reload keeps the list (ADR-0430).
  let loaded = $state(false);
  let runningTaskId = $state<string | null>(null);

  // Modal state: the task dialog, with the task being edited (null for a new one), and the history.
  let showTaskModal = $state(false);
  let editingTask = $state<ScheduledTask | null>(null);
  const history = new TaskHistory();

  onMount(async () => {
    await Promise.all([loadTasks(), loadResources(), loadTimeZone()]);
  });

  async function loadTimeZone() {
    try {
      timeZone = (await getScheduleTimezone()).timezone;
    } catch (e) {
      console.error("Failed to load the time zone of the schedules:", e);
    }
  }

  async function loadTasks() {
    loading = true;
    try {
      const result = await getTasks();
      tasks = Array.isArray(result) ? result : [];
    } catch (e) {
      console.error("Failed to load tasks:", e);
      tasks = [];
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

  async function handleDeleteTask(task: ScheduledTask) {
    if (!task.id) return;
    if (!confirm($t("scheduler.task.confirmDelete", { name: task.name }))) return;

    try {
      await deleteTask(task.id);
      await loadTasks();
    } catch (e: any) {
      console.error("Failed to delete task:", e);
      alert(e.message || $t("scheduler.task.deleteFailed"));
    }
  }

  async function handleToggleTask(task: ScheduledTask) {
    if (!task.id) return;

    try {
      await toggleTask(task.id);
      await loadTasks();
    } catch (e: any) {
      console.error("Failed to toggle task:", e);
      alert(e.message || $t("scheduler.task.toggleFailed"));
    }
  }

  async function handleRunNow(task: ScheduledTask) {
    if (!task.id) return;

    runningTaskId = task.id;
    try {
      await runTaskNow(task.id);
      // Show brief success indicator, then reload
      setTimeout(() => loadTasks(), 1000);
    } catch (e: any) {
      console.error("Failed to run task:", e);
      alert(e.message || $t("scheduler.task.runFailed"));
    }
    runningTaskId = null;
  }
</script>

<div class="space-y-4">
  <!-- Header -->
  <div class="flex flex-wrap items-center justify-between gap-3">
    <h2 class="text-xl font-semibold">{$t("scheduler.title")}</h2>
    <div class="flex flex-wrap gap-2">
      <Button variant="secondary" size="sm" onclick={() => history.show()}>
        <History class="w-4 h-4" />
        {$t("scheduler.history")}
      </Button>
      <Button variant="secondary" size="icon" onclick={loadTasks} title={$t("common.refresh")}>
        <RefreshCw class="w-4 h-4 {loading ? 'animate-spin' : ''}" />
      </Button>
      <Button variant="primary" size="sm" onclick={openCreateModal}>
        <Plus class="w-4 h-4" />
        {$t("scheduler.newTask")}
      </Button>
    </div>
  </div>
  {#if timeZone}
    <p class="text-sm text-content-secondary">{$t("scheduler.timeZone", { zone: timeZone })}</p>
  {/if}

  <!-- Task List -->
  <div class="card">
    {#if !loaded}
      <div aria-busy="true"><Skeleton count={3} size="control" /></div>
    {:else if tasks.length === 0}
      <div class="text-center py-8 text-content-secondary">
        <Calendar class="w-10 h-10 mx-auto mb-2 opacity-50" />
        <p>{$t("scheduler.empty")}</p>
        <p class="text-sm mt-1">{$t("scheduler.emptyHint")}</p>
      </div>
    {:else}
      <div class="space-y-2">
        {#each tasks as task}
          <TaskRow
            {task}
            running={runningTaskId === task.id}
            ontoggle={handleToggleTask}
            onrun={handleRunNow}
            onhistory={(task) => history.show(task.id, task.name)}
            onedit={openEditModal}
            ondelete={handleDeleteTask}
          />
        {/each}
      </div>
    {/if}
  </div>
</div>

<!-- Create/Edit Task Modal -->
{#if showTaskModal}
  <TaskFormDialog task={editingTask} {pools} {disks} {disksLoaded} {timeZone} onclose={closeTaskModal} onsaved={taskSaved} />
{/if}

<!-- History Modal -->
{#if history.open}
  <TaskHistoryDialog {history} />
{/if}
