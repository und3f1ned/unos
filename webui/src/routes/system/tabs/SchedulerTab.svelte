<script lang="ts">
  import { onMount } from "svelte";
  import { SvelteSet } from "svelte/reactivity";
  import {
    getTasks,
    deleteTask,
    runTaskNow,
    toggleTask,
    getScheduleTimezone,
    type ScheduledTask,
  } from "$lib/api/scheduler";
  import { getPools, getDisks, type Pool, type Disk } from "$lib/api/storage";
  import { Button, EmptyState, Skeleton } from "$lib/components/ui";
  import { TaskFormDialog, TaskHistory, TaskHistoryDialog, TaskRow } from "$lib/components/system/scheduler";
  import { t } from "$lib/i18n";
  import { Calendar, RefreshCw, Plus, History } from "lucide-svelte";
  import { knownZone } from "$lib/components/system/power";

  // State
  let tasks = $state<ScheduledTask[]>([]);
  let pools = $state<Pool[]>([]);
  let disks = $state<Disk[]>([]);
  let disksLoaded = $state(false);
  // The time zone whose clock the schedules follow (the NAS's); empty until it is known.
  let timeZone = $state("");
  // The NAS's zone when this browser knows it: the next runs and the history are on its clock.
  let zone = $derived(knownZone(timeZone));
  let loading = $state(true);
  // The first load has answered: a reload keeps the list (ADR-0430).
  let loaded = $state(false);
  let runningTaskId = $state<string | null>(null);
  // The tasks whose on/off the server has not answered yet: each shows its new state, and takes no
  // second change until the answer.
  const toggling = new SvelteSet<string>();

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
    const id = task.id;
    if (!id || toggling.has(id)) return;

    // The new state at once; the stored one again if the server refuses it.
    const was = task.enabled;
    task.enabled = !was;
    toggling.add(id);
    try {
      task.enabled = (await toggleTask(id)).enabled;
      await loadTasks();
    } catch (e: any) {
      task.enabled = was;
      console.error("Failed to toggle task:", e);
      alert(e.message || $t("scheduler.task.toggleFailed"));
    } finally {
      toggling.delete(id);
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
  <!-- The header: one time zone for the tab, the NAS's, named once. -->
  <div class="flex flex-wrap items-start justify-between gap-3">
    <div class="min-w-0">
      <h2 class="text-xl font-semibold">{$t("scheduler.title")}</h2>
      <!-- Only a zone this browser knows: the times are on its clock then, else on the browser's. -->
      {#if zone}
        <p class="page-subtitle">{$t("scheduler.timeZone", { zone })}</p>
      {/if}
    </div>
    <div class="flex flex-wrap gap-2">
      <Button variant="ghost" size="icon" icon={RefreshCw} loading={loading} onclick={loadTasks} title={$t("common.refresh")}>
        <span class="sr-only">{$t("common.refresh")}</span>
      </Button>
      <Button variant="secondary" icon={History} onclick={() => history.show()}>
        {$t("scheduler.history")}
      </Button>
      <Button variant="primary" icon={Plus} onclick={openCreateModal}>
        {$t("scheduler.newTask")}
      </Button>
    </div>
  </div>

  <!-- Task List -->
  <div class="card">
    {#if !loaded}
      <div aria-busy="true"><Skeleton shape="row" count={3} /></div>
    {:else if tasks.length === 0}
      <EmptyState compact icon={Calendar} title={$t("scheduler.empty")} description={$t("scheduler.emptyHint")} />
    {:else}
      <div class="divide-y divide-border -my-3">
        {#each tasks as task}
          <TaskRow
            {task}
            timeZone={zone}
            running={runningTaskId === task.id}
            toggling={toggling.has(task.id ?? "")}
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
  <TaskHistoryDialog {history} timeZone={zone} />
{/if}
