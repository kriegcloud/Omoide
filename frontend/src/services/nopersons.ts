import { DefinitiveMutationError } from "../stores/optimisticOperations";
import { API } from "../config";
import { NoPersonsPage } from "../types";
import type { MediaResolveResult } from "../types";

export type NoPersonsResolveAction = "DELETE_FILES" | "DELETE_RECORDS" | "BLACKLIST_RECORDS";

export interface NoPersonsQuery {
  cursor?: string | null;
  folder?: string | null;
  limit?: number;
  mediaType?: "image" | "video" | null;
  scope?: "processed" | "pending";
}

export const getNoPersonsMedia = async (options: NoPersonsQuery = {}): Promise<NoPersonsPage> => {
  const params = new URLSearchParams();
  if (options.folder) params.append("folder", options.folder);
  if (options.cursor) params.append("cursor", options.cursor);
  if (options.limit !== undefined) params.append("limit", options.limit.toString());
  if (options.mediaType) params.append("media_type", options.mediaType);
  if (options.scope) params.append("scope", options.scope);

  const response = await fetch(`${API}/api/nopersons?${params}`);
  if (!response.ok) throw new Error("Failed to fetch media without persons");
  return response.json();
};

export interface NoPersonsResolvePayload {
  action: NoPersonsResolveAction;
  operation_id?: string;
  media_ids?: number[];
  select_all?: boolean;
  folder?: string | null;
  media_type?: "image" | "video";
  scope?: "processed" | "pending";
}

export const resolveNoPersons = async (
  payload: NoPersonsResolvePayload
): Promise<MediaResolveResult> => {
  const response = await fetch(`${API}/api/nopersons/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    const message = err.detail || "Failed to resolve media";
    if ([400, 403, 422].includes(response.status)) throw new DefinitiveMutationError(message);
    throw new Error(message);
  }
  const result = await response.json();
  return result;
};
