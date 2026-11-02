import { jobError, observeSubmittedJobs, submitJob } from "./taskOperations";
import { API } from "../config";
import { Task, TaskFailure, TaskType } from "../types";

export const getActiveTasks = async (): Promise<Task[]> => {
  const res = await fetch(`${API}/api/tasks/active`);
  if (!res.ok) throw new Error("Failed to fetch active tasks");
  const rows: Task[] = await res.json();
  observeSubmittedJobs("tasks", rows);
  return rows;
};

export const getRecentTasks = async (
  limit = 10,
  taskType?: TaskType
): Promise<Task[]> => {
  const params = new URLSearchParams({ limit: String(limit) });
  if (taskType) params.set("task_type", taskType);
  const res = await fetch(`${API}/api/tasks/recent?${params.toString()}`);
  if (!res.ok) throw new Error("Failed to fetch recent tasks");
  const rows: Task[] = await res.json();
  observeSubmittedJobs("tasks", rows);
  return rows;
};

export const startTask = async (type: TaskType): Promise<Task> => submitJob("tasks", `Start ${type}`, async () => {
  const res = await fetch(`${API}/api/tasks/${type}`, { method: "POST" });
  if (!res.ok) throw jobError(res, `Failed to start task ${type}`);
  return res.json();
}, async () => { await Promise.all([getActiveTasks(), getRecentTasks()]); });

export const runProcessor = async (
  processorName: string,
  force = false
): Promise<Task> => submitJob("tasks", `Run ${processorName}`, async () => {
  const url = `${API}/api/tasks/run_processor/${processorName}?force=${force}`;
  const res = await fetch(url, { method: "POST" });
  if (!res.ok) throw jobError(res, `Failed to start processor ${processorName}`);
  return res.json();
}, async () => { await Promise.all([getActiveTasks(), getRecentTasks()]); });

export const runProcessorsForMedia = async (
  mediaIds: number[],
  processorNames: string[]
): Promise<Task> => {
  const body = JSON.stringify({ media_ids: [...mediaIds], processor_names: [...processorNames] });
  return submitJob("tasks", "Run processors for selection", async () => {
  const res = await fetch(`${API}/api/tasks/run_processors_for_media`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });
  if (!res.ok) throw jobError(res, await res.text());
  return res.json();
}, async () => { await Promise.all([getActiveTasks(), getRecentTasks()]); });
};

export const cancelTask = async (id: string): Promise<void> => submitJob("tasks", "Request task cancellation", async () => {
  const res = await fetch(`${API}/api/tasks/${id}/cancel`, { method: "POST" });
  if (!res.ok) throw jobError(res, `Failed to cancel task ${id}`);
}, async () => { await Promise.all([getActiveTasks(), getRecentTasks()]); });

export const resumeTask = async (id: string): Promise<Task> => submitJob("tasks", "Resume task", async () => {
  const res = await fetch(`${API}/api/tasks/${id}/resume`, { method: "POST" });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw jobError(res,
      typeof body?.detail === "string" ? body.detail : "Failed to resume task"
    );
  }
  return res.json();
}, async () => { await Promise.all([getActiveTasks(), getRecentTasks()]); });

export const getTaskFailures = async (id: string): Promise<TaskFailure[]> => {
  const res = await fetch(`${API}/api/tasks/${id}/failures`);
  if (!res.ok) throw new Error(`Failed to fetch failures for task ${id}`);
  return res.json();
};
