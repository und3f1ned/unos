import { getTaskRuns, getTaskRunsForTask, type TaskRun } from "$lib/api/scheduler";

/** The runs that the history dialog shows: of every task, or of one. The page creates it. */
export class TaskHistory {
  open = $state(false);
  runs = $state<TaskRun[]>([]);
  // The task whose runs are shown; null for every task.
  taskId = $state<string | null>(null);
  taskName = $state("");

  async load(taskId?: string, taskName?: string) {
    try {
      if (taskId) {
        this.runs = await getTaskRunsForTask(taskId);
        this.taskId = taskId;
        this.taskName = taskName || "";
      } else {
        this.runs = await getTaskRuns();
        this.taskId = null;
        this.taskName = "";
      }
    } catch (e) {
      console.error("Failed to load history:", e);
      this.runs = [];
    }
  }

  /** Opens the dialog once the runs have loaded. */
  async show(taskId?: string, taskName?: string) {
    await this.load(taskId, taskName);
    this.open = true;
  }

  close() {
    this.open = false;
    this.runs = [];
    this.taskId = null;
  }
}
