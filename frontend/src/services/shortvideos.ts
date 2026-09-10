import { API } from "../config";
import { ShortVideoPage } from "../types";
import type { MediaResolveResult } from "../types";

export type ShortVideoResolveAction = "DELETE_FILES" | "DELETE_RECORDS" | "BLACKLIST_RECORDS";

export interface ShortVideoQuery {
  maxDuration?: number;
  cursor?: string | null;
  folder?: string | null;
  limit?: number;
}

export const getShortVideos = async (options: ShortVideoQuery = {}): Promise<ShortVideoPage> => {
  const params = new URLSearchParams();
  if (options.folder) params.append("folder", options.folder);
  if (options.maxDuration !== undefined) params.append("max_duration", options.maxDuration.toString());
  if (options.cursor) params.append("cursor", options.cursor);
  if (options.limit !== undefined) params.append("limit", options.limit.toString());

  const response = await fetch(`${API}/api/shortvideos?${params}`);
  if (!response.ok) throw new Error("Failed to fetch short videos");
  return response.json();
};

export interface ShortVideoResolvePayload {
  action: ShortVideoResolveAction;
  media_ids?: number[];
  select_all?: boolean;
  folder?: string | null;
  max_duration?: number;
}

export const resolveShortVideos = async (
  payload: ShortVideoResolvePayload
): Promise<MediaResolveResult> => {
  const response = await fetch(`${API}/api/shortvideos/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to resolve short videos");
  }
  return response.json();
};
