import { API } from "../config";
import type { ImageRepairJob, RepairProfile } from "../types";

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    throw new Error(typeof detail === "string" ? detail : detail?.message ?? `Request failed (${response.status})`);
  }
  return response.json();
}

export const startRepair = (mediaId: number, profile: RepairProfile, personId?: number) =>
  fetch(`${API}/api/repairs/media/${mediaId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile, ...(personId ? { person_id: personId } : {}) }),
  }).then(json<ImageRepairJob>);

export const startBulkRepair = (mediaIds: number[], profile: RepairProfile, personId?: number) =>
  fetch(`${API}/api/repairs/bulk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ media_ids: mediaIds, profile, ...(personId ? { person_id: personId } : {}) }),
  }).then(json<ImageRepairJob[]>);

export const listRepairs = (options: { resultMediaId?: number; limit?: number } = {}) => {
  const params = new URLSearchParams({ limit: String(options.limit ?? 50) });
  if (options.resultMediaId) params.set("result_media_id", String(options.resultMediaId));
  return fetch(`${API}/api/repairs/?${params}`).then(
    json<{ items: ImageRepairJob[]; next_cursor: string | null }>,
  );
};

export const cancelRepair = (jobId: string) =>
  fetch(`${API}/api/repairs/${jobId}/cancel`, { method: "POST" }).then(json<ImageRepairJob>);
