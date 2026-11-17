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
    getScheduleTimezone,
    type ScheduledTask,
    type TaskRun,
    type TaskType,
    type ScheduleFrequency,
  } from "$lib/api/scheduler";
  import { getPools, getDisks, type Pool, type Disk } from "$lib/api/storage";
  import { Input } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
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
    UploadCloud,
  } from "lucide-svelte";

  // State
  let tasks: ScheduledTask[] = [];
  let runs: TaskRun[] = [];
  let pools: Pool[] = [];
  let disks: Disk[] = [];
  let disksLoaded = false;
  // The time zone whose clock the schedules follow (the NAS's); empty until it is known.
  let timeZone = "";
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
  // The placeholder of a custom task's command: a path, the same in every language.
  const COMMAND_EXAMPLE = "/usr/local/bin/my-script.sh";

  // A backup task being edited keeps its passphrase when the device has its
  // key. A new one, a task made a backup task, and a backup task without a key
  // (made before backups were encrypted, or its key was lost) need one.
  $: keepsPassphrase = editingTask?.task_type === "backup" && editingTask.has_backup_key === true;
  $: passphraseTooShort = passphrase !== "" && passphrase.length < MIN_PASSPHRASE_LENGTH;
  $: passphraseMismatch = passphraseConfirm !== passphrase;
  // The hint that names the time zone of the schedule's times, once it is shown.
  $: timeZoneHint = timeZone ? "task-time-zone-hint" : undefined;
  $: waitsForPassphrase =
    formData.task_type === "backup" &&
    (passphraseTooShort || passphraseMismatch || (!passphrase && !keepsPassphrase));

  // The task types, frequencies and days of the week, as the server names them, with the keys of
  // their labels in the catalog.
  const TASK_TYPES: { value: TaskType; key: string; icon: typeof Database }[] = [
    { value: "snapshot", key: "scheduler.type.snapshot", icon: Database },
    { value: "scrub", key: "scheduler.type.scrub", icon: Search },
    { value: "smart_test", key: "scheduler.type.smartTest", icon: HardDrive },
    { value: "backup", key: "scheduler.type.backup", icon: Archive },
    { value: "offsite_copy", key: "scheduler.type.offsiteCopy", icon: UploadCloud },
    { value: "custom", key: "scheduler.type.custom", icon: Terminal },
  ];

  const FREQUENCIES: { value: ScheduleFrequency; key: string }[] = [
    { value: "hourly", key: "scheduler.frequency.hourly" },
    { value: "daily", key: "scheduler.frequency.daily" },
    { value: "weekly", key: "scheduler.frequency.weekly" },
    { value: "monthly", key: "scheduler.frequency.monthly" },
    { value: "custom", key: "scheduler.frequency.custom" },
  ];

  // As cron numbers them; `schedule` is the key of a weekly schedule on that day.
  const DAYS_OF_WEEK = [
    { value: 0, key: "scheduler.day.sunday", schedule: "scheduler.schedule.sundays" },
    { value: 1, key: "scheduler.day.monday", schedule: "scheduler.schedule.mondays" },
    { value: 2, key: "scheduler.day.tuesday", schedule: "scheduler.schedule.tuesdays" },
    { value: 3, key: "scheduler.day.wednesday", schedule: "scheduler.schedule.wednesdays" },
    { value: 4, key: "scheduler.day.thursday", schedule: "scheduler.schedule.thursdays" },
    { value: 5, key: "scheduler.day.friday", schedule: "scheduler.schedule.fridays" },
    { value: 6, key: "scheduler.day.saturday", schedule: "scheduler.schedule.saturdays" },
  ];

  // The status of a run (TaskRun.status), by value.
  const RUN_STATUSES: Record<string, string> = {
    success: "scheduler.runs.success",
    failed: "scheduler.runs.failed",
    running: "scheduler.runs.running",
  };

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
        historyTaskName = taskName || "";
      } else {
        runs = await getTaskRuns();
        historyTaskId = null;
        historyTaskName = "";
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
      alert($t("scheduler.form.nameRequired"));
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
      alert(e.message || $t("scheduler.form.saveFailed"));
    }
    saving = false;
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

  // The functions below that give texts are reactive, so that the list and the history follow a
  // change of the language.

  // A type without a label as it comes.
  $: getTaskTypeLabel = (type: TaskType): string => {
    const known = TASK_TYPES.find((option) => option.value === type);
    return known ? $t(known.key) : type;
  };

  function getTaskTypeIcon(type: TaskType) {
    return TASK_TYPES.find((option) => option.value === type)?.icon || Terminal;
  }

  // On the clock of the NAS's time zone. A weekly or monthly task without its day runs on the
  // server's default, Sunday or the 1st (get_cron_expression of scheduler/router.py).
  $: formatSchedule = (task: ScheduledTask): string => {
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
  };

  // A status without a label as it comes.
  $: getStatusLabel = (status: string): string =>
    Object.prototype.hasOwnProperty.call(RUN_STATUSES, status) ? $t(RUN_STATUSES[status]) : status;

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

  $: formatDuration = (start: string, end?: string): string => {
    if (!end) return $t("scheduler.runs.inProgress");
    const ms = new Date(end).getTime() - new Date(start).getTime();
    if (ms < 1000) return $t("scheduler.runs.milliseconds", { ms });
    if (ms < 60000) {
      // One decimal, with the decimal separator of the page's language.
      const seconds = (ms / 1000).toLocaleString($intlLocale, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
      return $t("scheduler.runs.seconds", { seconds });
    }
    return $t("scheduler.runs.minutes", { minutes: Math.floor(ms / 60000), seconds: Math.floor((ms % 60000) / 1000) });
  };
</script>

<div class="space-y-4">
  <!-- Header -->
  <div class="flex flex-wrap items-center justify-between gap-3">
    <h2 class="text-xl font-bold">{$t("scheduler.title")}</h2>
    <div class="flex flex-wrap gap-2">
      <button
        on:click={() => openHistoryModal()}
        class="flex items-center gap-2 px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm"
      >
        <History class="w-4 h-4" />
        {$t("scheduler.history")}
      </button>
      <button on:click={loadTasks} class="p-2 bg-slate-700 rounded-lg hover:bg-slate-600" title={$t("common.refresh")}>
        <RefreshCw class="w-4 h-4 {loading ? 'animate-spin' : ''}" />
      </button>
      <button
        on:click={openCreateModal}
        class="flex items-center gap-2 px-3 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm"
      >
        <Plus class="w-4 h-4" />
        {$t("scheduler.newTask")}
      </button>
    </div>
  </div>
  {#if timeZone}
    <p class="text-sm text-content-secondary">{$t("scheduler.timeZone", { zone: timeZone })}</p>
  {/if}

  <!-- Task List -->
  <div class="bg-slate-800 rounded-lg p-4">
    {#if loading}
      <div class="flex justify-center py-8">
        <RefreshCw class="w-6 h-6 animate-spin text-blue-500" />
      </div>
    {:else if tasks.length === 0}
      <div class="text-center py-8 text-slate-400">
        <Calendar class="w-10 h-10 mx-auto mb-2 opacity-50" />
        <p>{$t("scheduler.empty")}</p>
        <p class="text-sm mt-1">{$t("scheduler.emptyHint")}</p>
      </div>
    {:else}
      <div class="space-y-2">
        {#each tasks as task}
          {@const TypeIcon = getTaskTypeIcon(task.task_type)}
          <!-- Below sm the actions wrap below the task, which keeps the row's width (ADR-0302). -->
          <div class="flex flex-wrap gap-y-2 items-center justify-between p-3 bg-slate-700 rounded-lg">
            <div class="flex items-center gap-3 flex-1 max-sm:basis-full min-w-0">
              <!-- Enable/Disable Toggle -->
              <button
                on:click={() => handleToggleTask(task)}
                class="p-1.5 rounded hover:bg-slate-600 transition-colors {task.enabled
                  ? 'text-green-400'
                  : 'text-slate-500'}"
                title={task.enabled ? $t("scheduler.task.disable") : $t("scheduler.task.enable")}
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
                    {$t("scheduler.task.on", { target: task.target_pool })}
                  {:else if task.target_disk}
                    {$t("scheduler.task.on", { target: task.target_disk })}
                  {/if}
                  &bull; {formatSchedule(task)}
                </p>
              </div>

              <!-- Next Run -->
              {#if task.enabled && task.next_run}
                <div class="hidden sm:flex flex-col items-end text-xs text-slate-500">
                  <span class="flex items-center gap-1">
                    <Clock class="w-3 h-3" />
                    {$t("scheduler.task.nextRun")}
                  </span>
                  <span>{new Date(task.next_run).toLocaleString($intlLocale)}</span>
                </div>
              {/if}
            </div>

            <!-- Actions -->
            <div class="flex items-center gap-1 ml-2 max-sm:ml-auto">
              <button
                on:click={() => handleRunNow(task)}
                disabled={runningTaskId === task.id}
                class="p-2 text-yellow-400 hover:bg-slate-600 rounded disabled:opacity-50"
                title={$t("scheduler.task.runNow")}
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
                title={$t("scheduler.task.viewHistory")}
              >
                <History class="w-4 h-4" />
              </button>
              <button
                on:click={() => openEditModal(task)}
                class="p-2 text-blue-400 hover:bg-slate-600 rounded"
                title={$t("scheduler.task.edit")}
              >
                <Edit3 class="w-4 h-4" />
              </button>
              <button
                on:click={() => handleDeleteTask(task)}
                class="p-2 text-red-400 hover:bg-slate-600 rounded"
                title={$t("scheduler.task.delete")}
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
  <div class="modal-overlay p-4">
    <div class="bg-slate-800 rounded-lg w-full max-w-lg max-h-[90vh] overflow-y-auto">
      <!-- Modal Header -->
      <div class="flex items-center justify-between p-4 border-b border-slate-700">
        <h3 class="text-lg font-bold">{editingTask ? $t("scheduler.form.editTitle") : $t("scheduler.form.createTitle")}</h3>
        <button on:click={closeTaskModal} class="p-1 hover:bg-slate-700 rounded">
          <X class="w-5 h-5" />
        </button>
      </div>

      <!-- Modal Body -->
      <div class="p-4 space-y-4">
        <!-- Task Name -->
        <div>
          <label for="task-name" class="form-label">{$t("scheduler.form.name")}</label>
          <input
            id="task-name"
            type="text"
            bind:value={formData.name}
            placeholder={$t("scheduler.form.namePlaceholder")}
            class="form-input"
          />
        </div>

        <!-- Task Type -->
        <div>
          <label for="task-type" class="form-label">{$t("scheduler.form.type")}</label>
          <select
            id="task-type"
            bind:value={formData.task_type}
            class="form-input"
          >
            {#each TASK_TYPES as type}
              <option value={type.value}>{$t(type.key)}</option>
            {/each}
          </select>
        </div>

        <!-- Pool Selection (for snapshot/scrub) -->
        {#if formData.task_type === "snapshot" || formData.task_type === "scrub"}
          <div>
            <label for="task-target-pool" class="form-label">{$t("scheduler.form.pool")}</label>
            {#if pools.length === 0}
              <p class="text-sm text-yellow-400">{$t("scheduler.form.noPools")}</p>
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
            <label for="task-target-disk" class="form-label">{$t("scheduler.form.disk")}</label>
            {#if disks.length === 0 && !storedDisk}
              <p class="text-sm text-yellow-400">{$t("scheduler.form.noDisks")}</p>
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
                    <option value={storedDisk}>{disksLoaded ? $t("scheduler.form.notConnected", { disk: storedDisk }) : storedDisk}</option>
                  {/if}
                  {#each disks as disk}
                    <option value={disk.path}>{disk.path} - {disk.model || disk.name}</option>
                  {/each}
                </select>
              {/key}
            {/if}
          </div>
        {/if}

        <!-- Off-device copy: its target and folders are those of System > Backup & Restore (ADR-0337) -->
        {#if formData.task_type === "offsite_copy"}
          <p class="text-xs text-content-muted">{$t("scheduler.form.offsiteHint")}</p>
        {/if}

        <!-- Custom Command -->
        {#if formData.task_type === "custom"}
          <div>
            <label for="task-command" class="form-label">{$t("scheduler.form.command")}</label>
            <input
              id="task-command"
              type="text"
              bind:value={formData.command}
              placeholder={COMMAND_EXAMPLE}
              aria-describedby="task-command-hint"
              class="form-input font-mono text-sm"
            />
            <p id="task-command-hint" class="text-xs text-content-muted mt-1">{$t("scheduler.form.commandHint")}</p>
          </div>
        {/if}

        <!-- Keep Count (for snapshot/backup) -->
        {#if formData.task_type === "snapshot" || formData.task_type === "backup"}
          <div>
            <label for="task-keep-count" class="form-label">{$t("scheduler.form.keepCount")}</label>
            <input
              id="task-keep-count"
              type="number"
              bind:value={formData.keep_count}
              min="1"
              max="100"
              class="form-input"
            />
            <p class="text-xs text-slate-500 mt-1">
              {formData.task_type === "backup" ? $t("scheduler.form.keepBackups") : $t("scheduler.form.keepSnapshots")}
            </p>
          </div>
        {/if}

        <!-- Passphrase (for backup): the backups are encrypted with it -->
        {#if formData.task_type === "backup"}
          <Input
            id="task-passphrase"
            type="password"
            label={$t("scheduler.form.passphrase")}
            autocomplete="new-password"
            bind:value={passphrase}
            error={passphraseTooShort ? $t("scheduler.form.passphraseTooShort", { n: MIN_PASSPHRASE_LENGTH }) : ""}
            hint={keepsPassphrase
              ? $t("scheduler.form.passphraseKept")
              : $t("scheduler.form.passphraseNeeded", { n: MIN_PASSPHRASE_LENGTH })}
          />
          <Input
            id="task-passphrase-confirm"
            type="password"
            label={$t("scheduler.form.confirmPassphrase")}
            autocomplete="new-password"
            bind:value={passphraseConfirm}
            error={passphraseConfirm && passphraseMismatch ? $t("scheduler.form.passphraseMismatch") : ""}
            hint={$t("scheduler.form.typeAgain")}
          />
        {/if}

        <hr class="border-slate-700" />

        <!-- Schedule Frequency -->
        <div>
          <label for="task-frequency" class="form-label">{$t("scheduler.form.frequency")}</label>
          <select
            id="task-frequency"
            bind:value={formData.frequency}
            class="form-input"
          >
            {#each FREQUENCIES as freq}
              <option value={freq.value}>{$t(freq.key)}</option>
            {/each}
          </select>
        </div>

        <!-- Custom Cron Expression -->
        {#if formData.frequency === "custom"}
          <div>
            <label for="task-cron-expression" class="form-label">{$t("scheduler.form.cron")}</label>
            <input
              id="task-cron-expression"
              type="text"
              bind:value={formData.cron_expression}
              placeholder="0 2 * * *"
              aria-describedby={timeZoneHint}
              class="form-input font-mono"
            />
            <p class="text-xs text-slate-500 mt-1">{$t("scheduler.form.cronFormat")}</p>
          </div>
        {:else}
          <!-- Time Selection -->
          <div class="grid grid-cols-2 gap-4">
            {#if formData.frequency !== "hourly"}
              <div>
                <label for="task-hour" class="form-label">{$t("scheduler.form.hour")}</label>
                <select
                  id="task-hour"
                  aria-describedby={timeZoneHint}
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
              <label for="task-minute" class="form-label">{$t("scheduler.form.minute")}</label>
              <select
                id="task-minute"
                aria-describedby={timeZoneHint}
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
              <label for="task-day-of-week" class="form-label">{$t("scheduler.form.dayOfWeek")}</label>
              <select
                id="task-day-of-week"
                aria-describedby={timeZoneHint}
                bind:value={formData.day_of_week}
                class="form-input"
              >
                {#each DAYS_OF_WEEK as day}
                  <option value={day.value}>{$t(day.key)}</option>
                {/each}
              </select>
            </div>
          {/if}

          {#if formData.frequency === "monthly"}
            <div>
              <label for="task-day-of-month" class="form-label">{$t("scheduler.form.dayOfMonth")}</label>
              <select
                id="task-day-of-month"
                aria-describedby={timeZoneHint}
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
        {#if timeZone}
          <p id="task-time-zone-hint" class="text-xs text-content-muted">{$t("scheduler.form.onTheClock", { zone: timeZone })}</p>
        {/if}

        <!-- Enabled Toggle -->
        <div class="flex items-center gap-3">
          <input
            type="checkbox"
            id="task-enabled"
            bind:checked={formData.enabled}
            class="w-4 h-4 rounded bg-slate-900 border-slate-600 text-blue-500 focus:ring-blue-500"
          />
          <label for="task-enabled" class="text-sm">{$t("scheduler.task.enable")}</label>
        </div>
      </div>

      <!-- Modal Footer -->
      <div class="flex gap-3 p-4 border-t border-slate-700">
        <button on:click={closeTaskModal} class="flex-1 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg">
          {$t("common.cancel")}
        </button>
        <button
          on:click={handleSaveTask}
          disabled={saving || waitsForPassphrase}
          class="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded-lg flex items-center justify-center gap-2"
        >
          {#if saving}
            <RefreshCw class="w-4 h-4 animate-spin" />
            {$t("scheduler.form.saving")}
          {:else}
            {editingTask ? $t("scheduler.form.save") : $t("scheduler.form.create")}
          {/if}
        </button>
      </div>
    </div>
  </div>
{/if}

<!-- History Modal -->
{#if showHistoryModal}
  <div class="modal-overlay p-4">
    <div class="bg-slate-800 rounded-lg w-full max-w-2xl max-h-[80vh] flex flex-col">
      <!-- Modal Header -->
      <div class="flex items-center justify-between p-4 border-b border-slate-700">
        <h3 class="text-lg font-bold">
          {$t("scheduler.runs.title", {
            name: historyTaskId ? historyTaskName || $t("scheduler.runs.task") : $t("scheduler.runs.allTasks"),
          })}
        </h3>
        <button on:click={closeHistoryModal} class="p-1 hover:bg-slate-700 rounded">
          <X class="w-5 h-5" />
        </button>
      </div>

      <!-- Modal Body -->
      <div class="flex-1 overflow-y-auto p-4">
        {#if runs.length === 0}
          <div class="text-center py-8 text-slate-400">
            <History class="w-10 h-10 mx-auto mb-2 opacity-50" />
            <p>{$t("scheduler.runs.empty")}</p>
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
                      {getStatusLabel(run.status)}
                    </span>
                  </div>
                  <span class="text-xs text-slate-500">{formatDuration(run.started_at, run.finished_at)}</span>
                </div>
                <div class="flex flex-wrap gap-x-3 text-xs text-content-secondary">
                  <span>{new Date(run.started_at).toLocaleString($intlLocale)}</span>
                  {#if typeof run.exit_code === "number"}
                    <span>{$t("scheduler.runs.exitStatus", { code: run.exit_code })}</span>
                  {/if}
                </div>
                {#if run.error}
                  <div class="mt-2 p-2 bg-red-500/10 rounded text-xs text-red-400 font-mono break-all">
                    {run.error}
                  </div>
                {/if}
                {#if run.output}
                  <details class="mt-2">
                    <summary class="text-xs text-slate-500 cursor-pointer hover:text-slate-400">{$t("scheduler.runs.output")}</summary>
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
          {$t("scheduler.runs.close")}
        </button>
      </div>
    </div>
  </div>
{/if}
