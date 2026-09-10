import { API } from "../config";
import { BlurPage, Task } from "../types";
import type { MediaResolveResult } from "../types";

export type BlurResolveAction = "DELETE_FILES" | "DELETE_RECORDS" | "BLACKLIST_RECORDS";

export interface BlurQuery {
  threshold?: number;
  cursor?: string | null;
  folder?: string | null;
  limit?: number;
  mediaType?: "image" | "video" | null;
}

export const getBlurryMedia = async (options: BlurQuery = {}): Promise<BlurPage> => {
  const params = new URLSearchParams();
  if (options.folder) params.append("folder", options.folder);
  if (options.threshold !== undefined) params.append("threshold", options.threshold.toString());
  if (options.cursor) params.append("cursor", options.cursor);
  if (options.limit !== undefined) params.append("limit", options.limit.toString());
  if (options.mediaType) params.append("media_type", options.mediaType);

  const response = await fetch(`${API}/api/blur?${params}`);
  if (!response.ok) throw new Error("Failed to fetch blurry media");
  return response.json();
};

export interface BlurResolvePayload {
  action: BlurResolveAction;
  media_ids?: number[];
  select_all?: boolean;
  folder?: string | null;
  threshold?: number;
  media_type?: "image" | "video";
}

export const resolveBlurry = async (
  payload: BlurResolvePayload
): Promise<MediaResolveResult> => {
  const response = await fetch(`${API}/api/blur/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to resolve blurry media");
  }
  return response.json();
};

export const startBlurScoring = async (): Promise<Task> => {
  const response = await fetch(`${API}/api/tasks/compute_blur_scores`, { method: "POST" });
  if (!response.ok) throw new Error("Failed to start blur scoring");
  return response.json();
};
