import { API } from "../config";
import { Task } from "../types";

export const getTask = async (task_id: string): Promise<Task> => {
  const response = await fetch(`${API}/api/tasks/${task_id}`);
  if (!response.ok) throw new Error(`Failed to load task (${response.status})`);
  return response.json();
};
