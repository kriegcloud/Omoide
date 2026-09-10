import { mutationBus } from "../stores/mutationBus";
import { API } from "../config";
import { BrokenMediaPage, BrokenResolvePayload } from "../types";

export const getBrokenMedia = async (params: {
  cursor?: string | null;
  limit?: number;
}): Promise<BrokenMediaPage> => {
  const query = new URLSearchParams();
  if (params.cursor) query.set("cursor", params.cursor);
  if (params.limit) query.set("limit", String(params.limit));
  const res = await fetch(`${API}/api/broken?${query}`);
  if (!res.ok) throw new Error("Failed to load broken media");
  return res.json();
};

export const resolveBroken = async (
  payload: BrokenResolvePayload
): Promise<{ removed: number }> => {
  const res = await fetch(`${API}/api/broken/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to resolve broken media");
  }
  const result = await res.json();
  if (Array.isArray(result.processed_ids)) mutationBus.emit({ type: "media:deleted", ids: result.processed_ids });
  else mutationBus.emit({ type: "list:invalidate", prefix: "" });
  return result;
};

export const retryBroken = async (payload: {
  media_ids?: number[];
  select_all?: boolean;
  after_id?: number;
}): Promise<{
  retried: number;
  cleared: number;
  still_broken: number;
  remaining?: number;
  next_cursor?: number | null;
}> => {
  const res = await fetch(`${API}/api/broken/retry`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to retry broken media");
  }
  return res.json();
};
