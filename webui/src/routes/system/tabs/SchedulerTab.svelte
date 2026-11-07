<script lang="ts">
  import { onMount } from "svelte";
  import {
    getTasks,
    createTask,
    updateTask,
    deleteTask,
    runTaskNow,
    toggleTask,
    getTaskRuns,
    getTaskRunsForTask,
    type ScheduledTask,
    type TaskRun,
    type TaskType,
    type ScheduleFrequency,
  } from "$lib/api/scheduler";
  import { getPools, getDisks, type Pool, type Disk } from "$lib/api/storage";
  import { Input } from "$lib/components/ui";
  import {
    Calendar,
    Clock,
    Play,
    Pause,
    RefreshCw,
    Plus,
    Trash2,
    Edit3,
    History,
    Zap,
    CheckCircle,
    XCircle,
    AlertTriangle,
    X,
    HardDrive,
    Database,
    Terminal,
    Archive,
    Search,
  } from "lucide-svelte";

  // State
  let tasks: ScheduledTask[] = [];
  let runs: TaskRun[] = [];
  let pools: Pool[] = [];
  let disks: Disk[] = [];
  let disksLoaded = false;
  let loading = true;
  let saving = false;
  let runningTaskId: string | null = null;

  // Modal state
  let showTaskModal = false;
  let showHistoryModal = false;
  let editingTask: ScheduledTask | null = null;
  let historyTaskId: string | null = null;
  let historyTaskName: string = "";

  // Form state
  let formData: Partial<ScheduledTask> = getDefaultFormData();
  // The passphrase of a backup task, typed twice. The API never returns it:
  // the form starts empty, and an edit that leaves it empty keeps the stored one.
  let passphrase = "";
  let passphraseConfirm = "";

  // At least as long as the server asks (MIN_PASSPHRASE_LENGTH in scheduler/router.py).
  const MIN_PASSPHRASE_LENGTH = 8;

  // A backup task being edited keeps its passphrase when the device has its
  // key. A new one, a task made a backup task, and a backup task without a key
  // (made before backups were encrypted, or its key was lost) need one.
  $: keepsPassphrase = editingTask?.task_type === "backup" && editingTask.has_backup_key === true;
  $: passphraseTooShort = passphrase !== "" && passphrase.length < MIN_PASSPHRASE_LENGTH;
  $: passphraseMismatch = passphraseConfirm !== passphrase;
  $: waitsForPassphrase =
    formData.task_type === "backup" &&
    (passphraseTooShort || passphraseMismatch || (!passphrase && !keepsPassphrase));

  const TASK_TYPES: { value: TaskType; label: string; icon: typeof Database }[] = [
    { value: "snapshot", label: "BTRFS Snapshot", icon: Database },
    { value: "scrub", label: "BTRFS Scrub", icon: Search },
    { value: "smart_test", label: "SMART Test", icon: HardDrive },
    { value: "backup", label: "System Backup", icon: Archive },
    { value: "custom", label: "Custom Command", icon: Terminal },
  ];

  const FREQUENCIES: { value: ScheduleFrequency; label: string }[] = [
    { value: "hourly", label: "Hourly" },
    { value: "daily", label: "Daily" },
    { value: "weekly", label: "Weekly" },
    { value: "monthly", label: "Monthly" },
    { value: "custom", label: "Custom (Cron)" },
  ];

  const DAYS_OF_WEEK = [
    { value: 0, label: "Sunday" },
    { value: 1, label: "Monday" },
    { value: 2, label: "Tuesday" },
    { value: 3, label: "Wednesday" },
    { value: 4, label: "Thursday" },
    { value: 5, label: "Friday" },
    { value: 6, label: "Saturday" },
  ];

  function getDefaultFormData(): Partial<ScheduledTask> {
    return {
      name: "",
      task_type: "snapshot",
      enabled: true,
      frequency: "daily",
      hour: 2,
      minute: 0,
      day_of_week: 0,
      day_of_month: 1,
      cron_expression: "0 2 * * *",
      target_pool: "",
      target_disk: "",
      command: "",
      keep_count: 7,
    };
  }

  onMount(async () => {
    await Promise.all([loadTasks(), loadResources()]);
  });

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

  async function loadHistory(taskId?: string, taskName?: string) {
    try {
      if (taskId) {
        runs = await getTaskRunsForTask(taskId);
        historyTaskId = taskId;
        historyTaskName = taskName || "Task";
      } else {
        runs = await getTaskRuns();
        historyTaskId = null;
        historyTaskName = "All Tasks";
      }
    } catch (e) {
      console.error("Failed to load history:", e);
      runs = [];
    }
  }

  function openCreateModal() {
    editingTask = null;
    formData = getDefaultFormData();
    if (pools.length > 0) formData.target_pool = pools[0].name;
    if (disks.length > 0) formData.target_disk = disks[0].path;
    passphrase = passphraseConfirm = "";
    showTaskModal = true;
  }

  // The disk of the SMART task being edited when the disk list does not have
  // it (not connected, or the list is not loaded): it stays a choice, so
  // saving keeps it. The backend checks the disk again when the task runs.
  $: storedDisk =
    editingTask?.task_type === "smart_test" &&
    editingTask.target_disk &&
    !disks.some((d) => d.path === editingTask?.target_disk)
      ? editingTask.target_disk
      : null;

  function openEditModal(task: ScheduledTask) {
    editingTask = task;
    formData = { ...task };
    passphrase = passphraseConfirm = "";
    showTaskModal = true;
  }

  function closeTaskModal() {
    showTaskModal = false;
    editingTask = null;
    formData = getDefaultFormData();
    passphrase = passphraseConfirm = "";
  }

  async function openHistoryModal(taskId?: string, taskName?: string) {
    await loadHistory(taskId, taskName);
    showHistoryModal = true;
  }

  function closeHistoryModal() {
    showHistoryModal = false;
    runs = [];
    historyTaskId = null;
  }

  async function handleSaveTask() {
    if (!formData.name?.trim()) {
      alert("Task name is required");
      return;
    }

    saving = true;
    try {
      const taskData: ScheduledTask = {
        name: formData.name!.trim(),
        task_type: formData.task_type as TaskType,
        enabled: formData.enabled ?? true,
        frequency: formData.frequency as ScheduleFrequency,
        hour: formData.hour ?? 0,
        minute: formData.minute ?? 0,
        day_of_week: formData.day_of_week,
        day_of_month: formData.day_of_month,
        cron_expression: formData.frequency === "custom" ? formData.cron_expression : undefined,
        target_pool: ["snapshot", "scrub"].includes(formData.task_type!) ? formData.target_pool : undefined,
        target_disk: formData.task_type === "smart_test" ? formData.target_disk : undefined,
        command: formData.task_type === "custom" ? formData.command : undefined,
        keep_count: ["snapshot", "backup"].includes(formData.task_type!) ? formData.keep_count : undefined,
        // Sent only when typed: left out, the server keeps the stored passphrase.
        passphrase: formData.task_type === "backup" && passphrase ? passphrase : undefined,
      };

      if (editingTask?.id) {
        await updateTask(editingTask.id, taskData);
      } else {
        await createTask(taskData);
      }

      closeTaskModal();
      await loadTasks();
    } catch (e: any) {
      console.error("Failed to save task:", e);
      alert(e.message || "Failed to save task");
    }
    saving = false;
  }

  async function handleDeleteTask(task: ScheduledTask) {
    if (!task.id) return;
    if (!confirm(`Delete task "${task.name}"?`)) return;

    try {
      await deleteTask(task.id);
      await loadTasks();
    } catch (e: any) {
      console.error("Failed to delete task:", e);
      alert(e.message || "Failed to delete task");
    }
  }

  async function handleToggleTask(task: ScheduledTask) {
    if (!task.id) return;

    try {
      await toggleTask(task.id);
      await loadTasks();
    } catch (e: any) {
      console.error("Failed to toggle task:", e);
      alert(e.message || "Failed to toggle task");
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
      alert(e.message || "Failed to run task");
    }
    runningTaskId = null;
  }

  function getTaskTypeLabel(type: TaskType): string {
    return TASK_TYPES.find((t) => t.value === type)?.label || type;
  }

  function getTaskTypeIcon(type: TaskType) {
    return TASK_TYPES.find((t) => t.value === type)?.icon || Terminal;
  }

  function getFrequencyLabel(freq: ScheduleFrequency): string {
    return FREQUENCIES.find((f) => f.value === freq)?.label || freq;
  }

  function formatSchedule(task: ScheduledTask): string {
    if (task.frequency === "custom" && task.cron_expression) {
      return task.cron_expression;
    }

    const time = `${String(task.hour).padStart(2, "0")}:${String(task.minute).padStart(2, "0")}`;

    switch (task.frequency) {
      case "hourly":
        return `Every hour at :${String(task.minute).padStart(2, "0")}`;
      case "daily":
        return `Daily at ${time}`;
      case "weekly":
        const day = DAYS_OF_WEEK.find((d) => d.value === task.day_of_week)?.label || "";
        return `${day}s at ${time}`;
      case "monthly":
        return `Day ${task.day_of_month} at ${time}`;
      default:
        return task.frequency;
    }
  }

  function getStatusColor(status?: string): string {
    switch (status) {
      case "success":
        return "text-green-400";
      case "failed":
        return "text-red-400";
      case "running":
        return "text-yellow-400";
      default:
        return "text-slate-500";
    }
  }

  function formatDuration(start: string, end?: string): string {
    if (!end) return "Running...";
    const ms = new Date(end).getTime() - new Date(start).getTime();
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
  }
</script>

<div class="space-y-4">
  <!-- Header -->
  <div class="flex items-center justify-between">
    <h2 class="text-xl font-bold">Scheduled Tasks</h2>
    <div class="flex gap-2">
      <button
        on:click={() => openHistoryModal()}
        class="flex items-center gap-2 px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm"
      >
        <History class="w-4 h-4" />
        History
      </button>
      <button on:click={loadTasks} class="p-2 bg-slate-700 rounded-lg hover:bg-slate-600" title="Refresh">
        <RefreshCw class="w-4 h-4 {loading ? 'animate-spin' : ''}" />
      </button>
      <button
        on:click={openCreateModal}
        class="flex items-center gap-2 px-3 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm"
      >
        <Plus class="w-4 h-4" />
        New Task
      </button>
    </div>
  </div>

  <!-- Task List -->
  <div class="bg-slate-800 rounded-lg p-4">
    {#if loading}
      <div class="flex justify-center py-8">
        <RefreshCw class="w-6 h-6 animate-spin text-blue-500" />
      </div>
    {:else if tasks.length === 0}
      <div class="text-center py-8 text-slate-400">
        <Calendar class="w-10 h-10 mx-auto mb-2 opacity-50" />
        <p>No scheduled tasks</p>
        <p class="text-sm mt-1">Create a task to automate snapshots, scrubs, and more</p>
      </div>
    {:else}
      <div class="space-y-2">
        {#each tasks as task}
          {@const TypeIcon = getTaskTypeIcon(task.task_type)}
          <div class="flex items-center justify-between p-3 bg-slate-700 rounded-lg">
            <div class="flex items-center gap-3 flex-1 min-w-0">
              <!-- Enable/Disable Toggle -->
              <button
                on:click={() => handleToggleTask(task)}
                class="p-1.5 rounded hover:bg-slate-600 transition-colors {task.enabled
                  ? 'text-green-400'
                  : 'text-slate-500'}"
                title={task.enabled ? "Disable task" : "Enable task"}
              >
                {#if task.enabled}
                  <Play class="w-5 h-5" />
                {:else}
                  <Pause class="w-5 h-5" />
                {/if}
              </button>

              <!-- Task Info -->
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2">
                  <TypeIcon class="w-4 h-4 text-blue-400 flex-shrink-0" />
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
                <p class="text-xs text-slate-400 truncate">
                  {getTaskTypeLabel(task.task_type)}
                  {#if task.target_pool}
                    on {task.target_pool}
                  {:else if task.target_disk}
                    on {task.target_disk}
                  {/if}
                  &bull; {formatSchedule(task)}
                </p>
              </div>

              <!-- Next Run -->
              {#if task.enabled && task.next_run}
                <div class="hidden sm:flex flex-col items-end text-xs text-slate-500">
                  <span class="flex items-center gap-1">
                    <Clock class="w-3 h-3" />
                    Next run
                  </span>
                  <span>{new Date(task.next_run).toLocaleString()}</span>
                </div>
              {/if}
            </div>

            <!-- Actions -->
            <div class="flex items-center gap-1 ml-2">
              <button
                on:click={() => handleRunNow(task)}
                disabled={runningTaskId === task.id}
                class="p-2 text-yellow-400 hover:bg-slate-600 rounded disabled:opacity-50"
                title="Run now"
              >
                {#if runningTaskId === task.id}
                  <RefreshCw class="w-4 h-4 animate-spin" />
                {:else}
                  <Zap class="w-4 h-4" />
                {/if}
              </button>
              <button
                on:click={() => openHistoryModal(task.id, task.name)}
                class="p-2 text-slate-400 hover:bg-slate-600 rounded"
                title="View history"
              >
                <History class="w-4 h-4" />
              </button>
              <button
                on:click={() => openEditModal(task)}
                class="p-2 text-blue-400 hover:bg-slate-600 rounded"
                title="Edit"
              >
                <Edit3 class="w-4 h-4" />
              </button>
              <button
                on:click={() => handleDeleteTask(task)}
                class="p-2 text-red-400 hover:bg-slate-600 rounded"
                title="Delete"
              >
                <Trash2 class="w-4 h-4" />
              </button>
            </div>
          </div>
        {/each}
      </div>
    {/if}
  </div>
</div>

<!-- Create/Edit Task Modal -->
{#if showTaskModal}
  <div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
    <div class="bg-slate-800 rounded-lg w-full max-w-lg max-h-[90vh] overflow-y-auto">
      <!-- Modal Header -->
      <div class="flex items-center justify-between p-4 border-b border-slate-700">
        <h3 class="text-lg font-bold">{editingTask ? "Edit Task" : "Create Task"}</h3>
        <button on:click={closeTaskModal} class="p-1 hover:bg-slate-700 rounded">
          <X class="w-5 h-5" />
        </button>
      </div>

      <!-- Modal Body -->
      <div class="p-4 space-y-4">
        <!-- Task Name -->
        <div>
          <label for="task-name" class="form-label">Task Name</label>
          <input
            id="task-name"
            type="text"
            bind:value={formData.name}
            placeholder="Daily snapshot"
            class="form-input"
          />
        </div>

        <!-- Task Type -->
        <div>
          <label for="task-type" class="form-label">Task Type</label>
          <select
            id="task-type"
            bind:value={formData.task_type}
            class="form-input"
          >
            {#each TASK_TYPES as type}
              <option value={type.value}>{type.label}</option>
            {/each}
          </select>
        </div>

        <!-- Pool Selection (for snapshot/scrub) -->
        {#if formData.task_type === "snapshot" || formData.task_type === "scrub"}
          <div>
            <label for="task-target-pool" class="form-label">Target Pool</label>
            {#if pools.length === 0}
              <p class="text-sm text-yellow-400">No pools available. Create a pool first.</p>
            {:else}
              <select
                id="task-target-pool"
                bind:value={formData.target_pool}
                class="form-input"
              >
                {#each pools as pool}
                  <option value={pool.name}>{pool.name}</option>
                {/each}
              </select>
            {/if}
          </div>
        {/if}

        <!-- Disk Selection (for smart_test) -->
        {#if formData.task_type === "smart_test"}
          <div>
            <label for="task-target-disk" class="form-label">Target Disk</label>
            {#if disks.length === 0 && !storedDisk}
              <p class="text-sm text-yellow-400">No disks available.</p>
            {:else}
              <!-- Made again when the extra option goes (the disk list arrived and has
                   the disk): Svelte selects the bound value when it changes, not when
                   the options change, and the browser would show the first disk. -->
              {#key storedDisk}
                <select
                  id="task-target-disk"
                  bind:value={formData.target_disk}
                  class="form-input"
                >
                  {#if storedDisk}
                    <option value={storedDisk}>{storedDisk}{disksLoaded ? " (not connected)" : ""}</option>
                  {/if}
                  {#each disks as disk}
                    <option value={disk.path}>{disk.path} - {disk.model || disk.name}</option>
                  {/each}
                </select>
              {/key}
            {/if}
          </div>
        {/if}

        <!-- Custom Command -->
        {#if formData.task_type === "custom"}
          <div>
            <label for="task-command" class="form-label">Command</label>
            <input
              id="task-command"
              type="text"
              bind:value={formData.command}
              placeholder="/usr/local/bin/my-script.sh"
              class="form-input font-mono text-sm"
            />
            <p class="text-xs text-slate-500 mt-1">Shell command to execute</p>
          </div>
        {/if}

        <!-- Keep Count (for snapshot/backup) -->
        {#if formData.task_type === "snapshot" || formData.task_type === "backup"}
          <div>
            <label for="task-keep-count" class="form-label">Keep Count</label>
            <input
              id="task-keep-count"
              type="number"
              bind:value={formData.keep_count}
              min="1"
              max="100"
              class="form-input"
            />
            <p class="text-xs text-slate-500 mt-1">Number of {formData.task_type}s to retain</p>
          </div>
        {/if}

        <!-- Passphrase (for backup): the backups are encrypted with it -->
        {#if formData.task_type === "backup"}
          <Input
            id="task-passphrase"
            type="password"
            label="Passphrase"
            autocomplete="new-password"
            bind:value={passphrase}
            error={passphraseTooShort ? `Use at least ${MIN_PASSPHRASE_LENGTH} characters` : ""}
            hint={keepsPassphrase
              ? "Leave empty to keep the current passphrase. A new one applies to the backups made from now on."
              : `Required, at least ${MIN_PASSPHRASE_LENGTH} characters. The backups are encrypted with it (AES-256-GCM). Keep it safe: a backup cannot be restored without it.`}
          />
          <Input
            id="task-passphrase-confirm"
            type="password"
            label="Confirm Passphrase"
            autocomplete="new-password"
            bind:value={passphraseConfirm}
            error={passphraseConfirm && passphraseMismatch ? "The passphrases do not match" : ""}
            hint="Type the passphrase again."
          />
        {/if}

        <hr class="border-slate-700" />

        <!-- Schedule Frequency -->
        <div>
          <label for="task-frequency" class="form-label">Frequency</label>
          <select
            id="task-frequency"
            bind:value={formData.frequency}
            class="form-input"
          >
            {#each FREQUENCIES as freq}
              <option value={freq.value}>{freq.label}</option>
            {/each}
          </select>
        </div>

        <!-- Custom Cron Expression -->
        {#if formData.frequency === "custom"}
          <div>
            <label for="task-cron-expression" class="form-label">Cron Expression</label>
            <input
              id="task-cron-expression"
              type="text"
              bind:value={formData.cron_expression}
              placeholder="0 2 * * *"
              class="form-input font-mono"
            />
            <p class="text-xs text-slate-500 mt-1">Format: minute hour day_of_month month day_of_week</p>
          </div>
        {:else}
          <!-- Time Selection -->
          <div class="grid grid-cols-2 gap-4">
            {#if formData.frequency !== "hourly"}
              <div>
                <label for="task-hour" class="form-label">Hour</label>
                <select
                  id="task-hour"
                  bind:value={formData.hour}
                  class="form-input"
                >
                  {#each Array(24) as _, i}
                    <option value={i}>{String(i).padStart(2, "0")}:00</option>
                  {/each}
                </select>
              </div>
            {/if}
            <div>
              <label for="task-minute" class="form-label">Minute</label>
              <select
                id="task-minute"
                bind:value={formData.minute}
                class="form-input"
              >
                {#each [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55] as m}
                  <option value={m}>{String(m).padStart(2, "0")}</option>
                {/each}
              </select>
            </div>
          </div>

          <!-- Day Selection -->
          {#if formData.frequency === "weekly"}
            <div>
              <label for="task-day-of-week" class="form-label">Day of Week</label>
              <select
                id="task-day-of-week"
                bind:value={formData.day_of_week}
                class="form-input"
              >
                {#each DAYS_OF_WEEK as day}
                  <option value={day.value}>{day.label}</option>
                {/each}
              </select>
            </div>
          {/if}

          {#if formData.frequency === "monthly"}
            <div>
              <label for="task-day-of-month" class="form-label">Day of Month</label>
              <select
                id="task-day-of-month"
                bind:value={formData.day_of_month}
                class="form-input"
              >
                {#each Array(28) as _, i}
                  <option value={i + 1}>{i + 1}</option>
                {/each}
              </select>
            </div>
          {/if}
        {/if}

        <!-- Enabled Toggle -->
        <div class="flex items-center gap-3">
          <input
            type="checkbox"
            id="task-enabled"
            bind:checked={formData.enabled}
            class="w-4 h-4 rounded bg-slate-900 border-slate-600 text-blue-500 focus:ring-blue-500"
          />
          <label for="task-enabled" class="text-sm">Enable task</label>
        </div>
      </div>

      <!-- Modal Footer -->
      <div class="flex gap-3 p-4 border-t border-slate-700">
        <button on:click={closeTaskModal} class="flex-1 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg">
          Cancel
        </button>
        <button
          on:click={handleSaveTask}
          disabled={saving || waitsForPassphrase}
          class="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded-lg flex items-center justify-center gap-2"
        >
          {#if saving}
            <RefreshCw class="w-4 h-4 animate-spin" />
            Saving...
          {:else}
            {editingTask ? "Save Changes" : "Create Task"}
          {/if}
        </button>
      </div>
    </div>
  </div>
{/if}

<!-- History Modal -->
{#if showHistoryModal}
  <div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
    <div class="bg-slate-800 rounded-lg w-full max-w-2xl max-h-[80vh] flex flex-col">
      <!-- Modal Header -->
      <div class="flex items-center justify-between p-4 border-b border-slate-700">
        <h3 class="text-lg font-bold">Run History: {historyTaskName}</h3>
        <button on:click={closeHistoryModal} class="p-1 hover:bg-slate-700 rounded">
          <X class="w-5 h-5" />
        </button>
      </div>

      <!-- Modal Body -->
      <div class="flex-1 overflow-y-auto p-4">
        {#if runs.length === 0}
          <div class="text-center py-8 text-slate-400">
            <History class="w-10 h-10 mx-auto mb-2 opacity-50" />
            <p>No run history</p>
          </div>
        {:else}
          <div class="space-y-2">
            {#each runs as run}
              <div class="p-3 bg-slate-700 rounded-lg">
                <div class="flex items-center justify-between mb-2">
                  <div class="flex items-center gap-2">
                    {#if run.status === "success"}
                      <CheckCircle class="w-4 h-4 text-green-400" />
                    {:else if run.status === "failed"}
                      <XCircle class="w-4 h-4 text-red-400" />
                    {:else}
                      <RefreshCw class="w-4 h-4 text-yellow-400 animate-spin" />
                    {/if}
                    <span class="font-medium text-sm">{run.task_name}</span>
                    <span class="text-xs px-2 py-0.5 rounded {run.status === 'success'
                      ? 'bg-green-500/20 text-green-400'
                      : run.status === 'failed'
                        ? 'bg-red-500/20 text-red-400'
                        : 'bg-yellow-500/20 text-yellow-400'}">
                      {run.status}
                    </span>
                  </div>
                  <span class="text-xs text-slate-500">{formatDuration(run.started_at, run.finished_at)}</span>
                </div>
                <div class="text-xs text-slate-400">
                  {new Date(run.started_at).toLocaleString()}
                </div>
                {#if run.error}
                  <div class="mt-2 p-2 bg-red-500/10 rounded text-xs text-red-400 font-mono break-all">
                    {run.error}
                  </div>
                {/if}
                {#if run.output}
                  <details class="mt-2">
                    <summary class="text-xs text-slate-500 cursor-pointer hover:text-slate-400">View output</summary>
                    <div class="mt-1 p-2 bg-slate-900 rounded text-xs text-slate-300 font-mono whitespace-pre-wrap break-all max-h-32 overflow-y-auto">
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
      <div class="p-4 border-t border-slate-700">
        <button on:click={closeHistoryModal} class="w-full px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg">
          Close
        </button>
      </div>
    </div>
  </div>
{/if}
