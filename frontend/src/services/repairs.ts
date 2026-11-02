import { jobError, observeSubmittedJobs, submitJob } from "./taskOperations";
import { mutationBus } from "../stores/mutationBus";
import { API } from "../config";
import type { ImageRepairJob, RepairProfile } from "../types";

// Keep a bounded observation history so polling announces each observed terminal
// transition once. Cold historical terminal rows establish a baseline silently.
const observedRepairs = new Map<string, { terminal: boolean }>();
const terminalRepairStatuses = new Set(["succeeded", "failed", "cancelled", "interrupted"]);
function observeRepair(job: ImageRepairJob, notify = true) {
  observeSubmittedJobs("repairs", [job]);
  const previous = observedRepairs.get(job.id);
  const terminal = terminalRepairStatuses.has(job.status);
  observedRepairs.delete(job.id);
  observedRepairs.set(job.id, { terminal: Boolean(previous?.terminal || terminal) });
  if (observedRepairs.size > 1000) observedRepairs.delete(observedRepairs.keys().next().value!);
  if (!notify || !previous || previous.terminal || !terminal) return;
  mutationBus.emit({ type: "repairs:changed" });
  if (job.result_media_id != null) {
    mutationBus.emit({ type: "media:updated", items: [{ id: job.result_media_id }] });
    mutationBus.emit({ type: "list:invalidate", prefix: "" });
  }
}

export interface RepairOptions {
  personId?: number;
  prompt?: string;
  seed?: number;
  randomizePrompts?: boolean;
  provider?: string;
  params?: Record<string, string | number>;
  hostedConsent?: boolean;
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    throw jobError(response, typeof detail === "string" ? detail : detail?.message ?? `Request failed (${response.status})`);
  }
  return response.json();
}

const repairBody = (profile: RepairProfile, options: RepairOptions) => ({
  profile,
  ...(options.provider ? { provider: options.provider, hosted_consent: options.hostedConsent === true } : {}),
  ...(options.personId ? { person_id: options.personId } : {}),
  params: {
    ...options.params,
    ...(options.prompt ? { prompt: options.prompt } : {}),
    ...(options.seed !== undefined ? { seed: options.seed } : {}),
  },
});

export const startRepair = (
  mediaId: number,
  profile: RepairProfile,
  options: RepairOptions = {},
) => {
  const body = JSON.stringify(repairBody(profile, options));
  return submitJob("repairs", "Submit repair", () => fetch(`${API}/api/repairs/media/${mediaId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  }).then(json<ImageRepairJob>).then((result) => {
    observeRepair(result, false);
    mutationBus.emit({ type: "repairs:changed" });
    return result;
  }), async () => { await listRepairs(); mutationBus.emit({ type: "repairs:changed" }); });
};

export const startBulkRepair = (
  mediaIds: number[],
  profile: RepairProfile,
  options: RepairOptions = {},
) => {
  const body = JSON.stringify({
    media_ids: [...mediaIds], ...repairBody(profile, options),
    ...(options.randomizePrompts ? { randomize_prompts: true } : {}),
  });
  return submitJob("repairs", "Submit repairs", () => fetch(`${API}/api/repairs/bulk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  }).then(json<ImageRepairJob[]>).then((result) => {
    result.forEach(job => observeRepair(job, false));
    mutationBus.emit({ type: "repairs:changed" });
    return result;
  }), async () => { await listRepairs(); mutationBus.emit({ type: "repairs:changed" }); });
};

export const listBackgroundPrompts = () =>
  fetch(`${API}/api/repairs/background-prompts`).then(json<string[]>);

export const listRepairs = (options: { resultMediaId?: number; limit?: number; cursor?: string | null } = {}) => {
  const params = new URLSearchParams({ limit: String(options.limit ?? 50) });
  if (options.cursor) params.set("cursor", options.cursor);
  if (options.resultMediaId) params.set("result_media_id", String(options.resultMediaId));
  return fetch(`${API}/api/repairs/?${params}`).then(
    json<{ items: ImageRepairJob[]; next_cursor: string | null }>,
  ).then(result => {
    result.items.forEach(job => observeRepair(job));
    return result;
  });
};

export const cancelRepair = (jobId: string) =>
  submitJob("repairs", "Request repair cancellation", () => fetch(`${API}/api/repairs/${jobId}/cancel`, { method: "POST" }).then(json<ImageRepairJob>).then((result) => {
    observeRepair(result, false);
    mutationBus.emit({ type: "repairs:changed" });
    return result;
  }), async () => { await getRepair(jobId); mutationBus.emit({ type: "repairs:changed" }); });

export interface UpscaleSelection {
  provider: string;
  params: Record<string, string | number>;
  hostedConsent: boolean;
}

export interface UpscalerParameter {
  type: "string" | "integer";
  default: string | number;
  min?: number;
  max?: number;
  choices?: (string | number)[];
  max_length?: number;
}
export interface UpscalerProvider {
  id: string;
  label: string;
  kind: "local" | "hosted";
  fidelity_note: string;
  profile: string;
  params: Record<string, UpscalerParameter>;
  available: boolean;
  reason?: string | null;
  warning?: string | null;
}
export const listUpscalers = () => fetch(`${API}/api/repairs/upscalers`).then(json<UpscalerProvider[]>);
export const getRepair = (id: string) => fetch(`${API}/api/repairs/${encodeURIComponent(id)}`).then(json<ImageRepairJob>).then(job => {
  observeRepair(job);
  return job;
});
