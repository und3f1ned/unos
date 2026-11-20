<script lang="ts">
  import { onMount } from "svelte";
  import { SvelteSet } from "svelte/reactivity";
  import { getTasks, deleteTask, runTaskNow, toggleTask, type ScheduledTask } from "$lib/api/scheduler";
  import { ApiError } from "$lib/api/client";
  import { getPools, getDisks, type Pool, type Disk } from "$lib/api/storage";
  import { Button, EmptyState, HelpText, LoadState, Skeleton, confirm, toast } from "$lib/components/ui";
  import { TaskFormDialog, TaskHistory, TaskHistoryDialog, TaskRow } from "$lib/components/system/scheduler";
  import { messageOf } from "$lib/errors";
  import { t } from "$lib/i18n";
  import { nasTimeZone } from "$lib/stores/nasTime";
  import { knownZone } from "$lib/time";
  import { Calendar, RefreshCw, Plus, History } from "lucide-svelte";

  // The tasks of the Scheduler, on the NAS's clock (ADR-0432): the tab names the zone once, and
  // every time it shows is the NAS's. A failed load says so in place of the list, never "no tasks";
  // what an action did or why it failed comes as a toast.
  let tasks = $state<ScheduledTask[]>([]);
  let pools = $state<Pool[]>([]);
  let disks = $state<Disk[]>([]);
  let disksLoaded = $state(false);
  // The NAS's zone when this browser knows it: the next runs and the history are on its clock, and
  // the tab names it; else the browser's clock, unnamed.
  let zone = $derived(knownZone($nasTimeZone) ? $nasTimeZone : null);
  let loading = $state(true);
  // The first load has answered: a reload keeps the list (ADR-0430).
  let loaded = $state(false);
  let loadError = $state("");
  let runningTaskId = $state<string | null>(null);
  // The tasks whose on/off the server has not answered yet: each shows its new state, and takes no
  // second change until the answer.
  const toggling = new SvelteSet<string>();

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
    const id = task.id;
    if (!id || toggling.has(id)) return;

    // The new state at once; the stored one again if the server refuses it.
    const was = task.enabled;
    task.enabled = !was;
    toggling.add(id);
    try {
      const { enabled } = await toggleTask(id);
      task.enabled = enabled;
      toast.success($t(enabled ? "scheduler.task.enabledNow" : "scheduler.task.disabledNow", { name: task.name }));
      await loadTasks();
    } catch (e) {
      task.enabled = was;
      failed("scheduler.task.toggleFailed", e);
    } finally {
      toggling.delete(id);
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
  <!-- The header; the zone of the tab's times is named once, under it. -->
  <div class="flex flex-wrap items-center justify-between gap-3">
    <h2 class="text-xl font-semibold">{$t("scheduler.title")}</h2>
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
  <!-- The zone of every time of the tab, once; its line keeps its height until it is known. -->
  <div class="min-h-[18px]">
    {#if zone}
      <HelpText text={$t("scheduler.timeZone", { zone })}>
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
      <div class="divide-y divide-border -my-3">
        {#each tasks as task (task.id)}
          <TaskRow
            {task}
            {zone}
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
    </LoadState>
  </section>
</div>

<!-- Create/Edit Task Modal -->
{#if showTaskModal}
  <TaskFormDialog task={editingTask} {pools} {disks} {disksLoaded} timeZone={zone ?? ""} onclose={closeTaskModal} onsaved={taskSaved} />
{/if}

<!-- History of the runs -->
<TaskHistoryDialog {history} {zone} />
