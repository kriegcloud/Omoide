import { mutationBus } from "../stores/mutationBus";
import { API } from "../config";
import { DuplicatePage, Task, DuplicateStats } from "../types";

export const getDuplicates = async (
  cursor: string | null = null,
  sortBy: "count" | "size" = "count",
  mediaType?: "image" | "video",
  limit: number = 10,
  minCount: number = 2,
  folder?: string | null,
): Promise<DuplicatePage> => {
  const params = new URLSearchParams();
  if (cursor) params.append("cursor", cursor);
  params.append("limit", limit.toString());
  if (sortBy !== "count") params.append("sort_by", sortBy);
  if (mediaType) params.append("media_type", mediaType);
  if (minCount > 2) params.append("min_count", minCount.toString());
  if (folder) params.append("folder", folder);

  const response = await fetch(`${API}/api/duplicates?${params.toString()}`);
  if (!response.ok) {
    throw new Error("Failed to fetch duplicates");
  }
  return response.json();
};

export const getDuplicateStats = async (folder?: string | null): Promise<DuplicateStats> => {
  const params = new URLSearchParams();
  if (folder) params.append("folder", folder);
  const response = await fetch(`${API}/api/duplicates/stats?${params}`);
  if (!response.ok) {
    throw new Error("Failed to fetch duplicate statistics");
  }
  return response.json();
};

export const startDuplicateDetection = async (): Promise<Task> => {
  const response = await fetch(`${API}/api/tasks/find_duplicates`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error("Failed to start duplicate detection");
  }
  return response.json();
};

export type ResolveAction =
  | "DELETE_FILES"
  | "DELETE_RECORDS"
  | "BLACKLIST_RECORDS"
  | "MARK_NOT_DUPLICATE";
export const resolveDuplicates = async (
  groupId: number,
  action: ResolveAction,
  masterMediaId?: number,
  memberIds: number[] = [],
) => {
  const payload = {
    group_id: groupId,
    master_media_id: masterMediaId ?? null,
    action: action,
  };

  const response = await fetch(`${API}/api/duplicates/resolve`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    // A 409 can follow committed per-member deletions. Re-read survivors everywhere.
    if (response.status === 409) mutationBus.emit({ type: "list:invalidate", prefix: "" });
    throw Object.assign(new Error(errorData.detail || "Failed to resolve duplicate group."), { status: response.status });
  }

  const result = await response.json();
  const mediaIds = action === "MARK_NOT_DUPLICATE" ? [] : memberIds.filter(id => id !== masterMediaId);
  if (mediaIds.length) mutationBus.emit({ type: "media:deleted", ids: mediaIds });
  mutationBus.emit({ type: "duplicates:resolved", groupId, mediaIds });
  if (action !== "MARK_NOT_DUPLICATE" && !memberIds.length) mutationBus.emit({ type: "list:invalidate", prefix: "" });
  return result;
};
