import { startOperation } from "../stores/optimisticOperations";
import { entityKey } from "../stores/queryKeys";
import { mutationBus } from "../stores/mutationBus";
import { apiFetch, Query } from "./api";
import { PageResponse } from "../stores/useListStore";

/** Endpoints of the maintenance review lists (each also has `/resolve`). */
export type ReviewEndpoint =
  | "/api/blur"
  | "/api/lowresolution"
  | "/api/noexifdate"
  | "/api/nopersons"
  | "/api/shortvideos"
  | "/api/untagged";

export type ReviewResolveAction =
  | "DELETE_FILES"
  | "DELETE_RECORDS"
  | "BLACKLIST_RECORDS";

/** List one page of a review category. `filters` use the backend's query
 * names and are sent unchanged to `/resolve`, so a bulk action applies to
 * exactly the filtered set. */
export const getReviewPage = <T>(
  endpoint: ReviewEndpoint,
  filters: Query,
  cursor: string | null,
  signal?: AbortSignal
) =>
  apiFetch<PageResponse<T>>(endpoint, {
    query: { ...filters, cursor, limit: 50 },
    signal,
    errorMessage: "Failed to load media",
  });

export const resolveReview = (
  endpoint: ReviewEndpoint,
  filters: Query,
  action: ReviewResolveAction,
  target: { mediaIds: number[] } | { selectAll: true }
) =>
  apiFetch<{ removed: number; processed_ids?: number[]; skipped_ids?: number[] }>(`${endpoint}/resolve`, {
    method: "POST",
    body: {
      ...filters,
      action,
      ...("selectAll" in target
        ? { select_all: true }
        : { media_ids: target.mediaIds }),
    },
    errorMessage: "Failed to resolve media",
  }).then((result) => {
    mutationBus.emit({ type: "review:changed", scope: endpoint });
    if (Array.isArray(result.processed_ids)) {
      mutationBus.emit({ type: "media:deleted", ids: result.processed_ids });
    } else {
      mutationBus.emit({ type: "list:invalidate", prefix: "" });
    }
    return result;
  });

/** Legacy review endpoints return exact members, but do not yet accept durable
 * operation IDs. Never infer applied identities from a count or replay a loss. */
export interface ReviewResolveResult {
  removed?: number;
  deleted?: number;
  cleared?: number;
  processed_ids?: number[];
  skipped_ids?: number[];
  errors?: { id: number; reason: string }[];
}

export function startReviewResolve({ label, ids, query, countOnly = false, request, refresh }: {
  label: string;
  ids: readonly number[];
  query?: string;
  countOnly?: boolean;
  request: () => Promise<ReviewResolveResult>;
  refresh?: () => Promise<void>;
}) {
  const selected = [...new Set(ids)];
  return startOperation({
    label,
    input: { ids: selected },
    resources: selected.map(id => entityKey("media", id)),
    // Missing status only affects its review query, never global media existence.
    patches: selected.map(id => ({ kind: "remove" as const, key: entityKey("media", id), query })),
    request: async (_input, operationId) => {
      const result = await request();
      if (countOnly) return { operation_id: operationId, state: "succeeded" as const, applied: [], skipped: [], failed: [], unknown: [] };
      if (!Array.isArray(result.processed_ids)) throw new Error("Missing processed identities; review outcome needs reconciliation");
      const applied = [...new Set(result.processed_ids)].map(id => entityKey("media", id));
      const failed = (result.errors ?? []).filter(item => !result.processed_ids!.includes(item.id))
        .map(item => ({ key: entityKey("media", item.id), code: "review_failed", message: item.reason }));
      const accounted = new Set([...applied, ...failed.map(item => item.key)]);
      const skipped = [...new Set(result.skipped_ids ?? [])].map(id => entityKey("media", id))
        .filter(key => !accounted.has(key)).map(key => ({ key, reason: "No longer matches or could not be processed" }));
      skipped.forEach(item => accounted.add(item.key));
      const unknown = selected.map(id => entityKey("media", id)).filter(key => !accounted.has(key));
      return { operation_id: operationId, state: unknown.length ? "unknown" as const : failed.length || skipped.length ? "partial" as const : "succeeded" as const, applied, skipped, failed, unknown };
    },
    receipt: result => result,
    // Count-only responses settle intent, then GET determines the actual rows.
    ...(countOnly ? { responsePatches: () => [] } : {}),
    events: (_receipt, applied) => countOnly ? [] : [{ type: "media:deleted" as const, ids: applied.map(key => Number(key.split(":")[1])) }],
    refresh,
  });
}
