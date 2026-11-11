import { api } from "./client";

export type TaskType = "snapshot" | "scrub" | "smart_test" | "backup" | "offsite_copy" | "custom";
export type ScheduleFrequency = "hourly" | "daily" | "weekly" | "monthly" | "custom";

export interface ScheduledTask {
  id?: string;
  name: string;
  task_type: TaskType;
  enabled: boolean;
  frequency: ScheduleFrequency;
  cron_expression?: string;
  hour: number;
  minute: number;
  day_of_week?: number;
  day_of_month?: number;
  target_pool?: string;
  target_disk?: string;
  command?: string;
  keep_count?: number;
  /**
   * Backup tasks: the passphrase their backups are encrypted with. Write-only:
   * sent to set it, never returned; an update without it keeps the stored one.
   */
  passphrase?: string;
  /** The start of the last run: ISO 8601 in UTC, with its offset. */
  last_run?: string;
  /**
   * In the answers: when the task runs next, by the clock of the NAS's time zone
   * (getScheduleTimezone), which its hour and minute follow; ISO 8601 with that
   * zone's offset. null when its schedule has no next time.
   */
  next_run?: string | null;
  last_status?: string;
  /**
   * In the answers, for backup tasks: whether the device has the key derived
   * from the passphrase, which an update without one keeps. null for other
   * tasks, and when the server cannot read its keys.
   */
  has_backup_key?: boolean | null;
}

export interface TaskRun {
  id: string;
  task_id: string;
  task_name: string;
  /** ISO 8601 in UTC, with its offset, as finished_at. */
  started_at: string;
  finished_at?: string;
  status: string;
  /** What a custom task's command printed (its last 16 KiB), or what another task reports. */
  output?: string | null;
  /** Why the run failed. */
  error?: string | null;
  /** The exit status of a custom task's command; null when it did not exit by itself, and for other tasks. */
  exit_code?: number | null;
}

export async function getTasks(): Promise<ScheduledTask[]> {
  return api.get<ScheduledTask[]>("/scheduler/tasks");
}

export async function createTask(task: ScheduledTask): Promise<ScheduledTask> {
  return api.post<ScheduledTask>("/scheduler/tasks", task);
}

export async function updateTask(id: string, task: ScheduledTask): Promise<ScheduledTask> {
  return api.put<ScheduledTask>(`/scheduler/tasks/${id}`, task);
}

export async function deleteTask(id: string): Promise<void> {
  return api.delete(`/scheduler/tasks/${id}`);
}

export async function runTaskNow(id: string): Promise<void> {
  return api.post(`/scheduler/tasks/${id}/run`);
}

export async function toggleTask(id: string): Promise<{ enabled: boolean }> {
  return api.post<{ enabled: boolean }>(`/scheduler/tasks/${id}/toggle`);
}

export async function getTaskRuns(): Promise<TaskRun[]> {
  return api.get<TaskRun[]>("/scheduler/runs");
}

export async function getTaskRunsForTask(taskId: string): Promise<TaskRun[]> {
  return api.get<TaskRun[]>(`/scheduler/runs/${taskId}`);
}

/**
 * The time zone whose clock the schedules follow: the NAS's, as it named it last when it cannot be
 * asked for a moment, or UTC when it names none that the supervisor can use.
 */
export async function getScheduleTimezone(): Promise<{ timezone: string }> {
  return api.get<{ timezone: string }>("/scheduler/timezone");
}
