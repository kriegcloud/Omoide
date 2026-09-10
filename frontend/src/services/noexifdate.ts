import { mutationBus } from "../stores/mutationBus";
import { API } from "../config";
import { NoExifDatePage } from "../types";
import type { MediaResolveResult } from "../types";

export type NoExifDateResolveAction = "DELETE_FILES" | "DELETE_RECORDS" | "BLACKLIST_RECORDS";

export interface NoExifDateQuery {
  cursor?: string | null;
  folder?: string | null;
  limit?: number;
  mediaType?: "image" | "video" | null;
}

export const getNoExifDateMedia = async (options: NoExifDateQuery = {}): Promise<NoExifDatePage> => {
  const params = new URLSearchParams();
  if (options.folder) params.append("folder", options.folder);
  if (options.cursor) params.append("cursor", options.cursor);
  if (options.limit !== undefined) params.append("limit", options.limit.toString());
  if (options.mediaType) params.append("media_type", options.mediaType);

  const response = await fetch(`${API}/api/noexifdate?${params}`);
  if (!response.ok) throw new Error("Failed to fetch media without EXIF date");
  return response.json();
};

export interface NoExifDateResolvePayload {
  action: NoExifDateResolveAction;
  media_ids?: number[];
  select_all?: boolean;
  folder?: string | null;
  media_type?: "image" | "video";
}

export const resolveNoExifDate = async (
  payload: NoExifDateResolvePayload
): Promise<MediaResolveResult> => {
  const response = await fetch(`${API}/api/noexifdate/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to resolve media");
  }
  const result = await response.json();
  if (Array.isArray(result.processed_ids)) mutationBus.emit({ type: "media:deleted", ids: result.processed_ids });
  else mutationBus.emit({ type: "list:invalidate", prefix: "" });
  return result;
};
