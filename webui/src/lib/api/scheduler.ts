import { api } from "./client";

export type TaskType = "snapshot" | "scrub" | "smart_test" | "backup" | "custom";
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
  last_run?: string;
  next_run?: string;
  last_status?: string;
}

export interface TaskRun {
  id: string;
  task_id: string;
  task_name: string;
  started_at: string;
  finished_at?: string;
  status: string;
  output?: string;
  error?: string;
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
