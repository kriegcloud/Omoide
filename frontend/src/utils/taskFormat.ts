import { Task } from "../types";

const STEP_LABELS: Record<string, string> = {
  clustering_batches: "Preparing clustering batches",
  clustering_batch: "Clustering faces",
  clustering_unassigned: "Clustering faces",
  merging_similar_persons: "Merging similar people",
  matching_known_persons: "Matching faces to known people",
  backfilling_face_quality: "Rating face quality",
  backfilling_demographics: "Predicting gender and age",
};

export function formatTaskStep(step?: string | null): string {
  if (!step) return "Starting";
  return STEP_LABELS[step] ?? step;
}

export function formatTaskDuration(seconds?: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  if (seconds < 10) return `${seconds.toFixed(1)} s`;
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return remainder ? `${minutes} min ${remainder} s` : `${minutes} min`;
}

export function formatRelativeTime(task: Task): string {
  const value = task.finished_at ?? task.started_at ?? task.created_at;
  if (!value) return "—";
  const elapsedSeconds = Math.max(
    0,
    (Date.now() - new Date(value).getTime()) / 1000
  );
  if (!Number.isFinite(elapsedSeconds) || elapsedSeconds < 60) return "just now";
  if (elapsedSeconds < 3600) return `${Math.floor(elapsedSeconds / 60)} min ago`;
  if (elapsedSeconds < 86400) return `${Math.floor(elapsedSeconds / 3600)} h ago`;
  if (elapsedSeconds < 172800) return "yesterday";
  return `${Math.floor(elapsedSeconds / 86400)} d ago`;
}
