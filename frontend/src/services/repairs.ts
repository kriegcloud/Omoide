import { API } from "../config";
import type { ImageRepairJob, RepairProfile } from "../types";

export interface RepairOptions {
  personId?: number;
  prompt?: string;
  seed?: number;
  randomizePrompts?: boolean;
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    throw new Error(typeof detail === "string" ? detail : detail?.message ?? `Request failed (${response.status})`);
  }
  return response.json();
}

const repairBody = (profile: RepairProfile, options: RepairOptions) => ({
  profile,
  ...(options.personId ? { person_id: options.personId } : {}),
  params: {
    ...(options.prompt ? { prompt: options.prompt } : {}),
    ...(options.seed !== undefined ? { seed: options.seed } : {}),
  },
});

export const startRepair = (
  mediaId: number,
  profile: RepairProfile,
  options: RepairOptions = {},
) =>
  fetch(`${API}/api/repairs/media/${mediaId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(repairBody(profile, options)),
  }).then(json<ImageRepairJob>);

export const startBulkRepair = (
  mediaIds: number[],
  profile: RepairProfile,
  options: RepairOptions = {},
) =>
  fetch(`${API}/api/repairs/bulk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      media_ids: mediaIds,
      ...repairBody(profile, options),
      ...(options.randomizePrompts ? { randomize_prompts: true } : {}),
    }),
  }).then(json<ImageRepairJob[]>);

export const listBackgroundPrompts = () =>
  fetch(`${API}/api/repairs/background-prompts`).then(json<string[]>);

export const listRepairs = (options: { resultMediaId?: number; limit?: number } = {}) => {
  const params = new URLSearchParams({ limit: String(options.limit ?? 50) });
  if (options.resultMediaId) params.set("result_media_id", String(options.resultMediaId));
  return fetch(`${API}/api/repairs/?${params}`).then(
    json<{ items: ImageRepairJob[]; next_cursor: string | null }>,
  );
};

export const cancelRepair = (jobId: string) =>
  fetch(`${API}/api/repairs/${jobId}/cancel`, { method: "POST" }).then(json<ImageRepairJob>);
