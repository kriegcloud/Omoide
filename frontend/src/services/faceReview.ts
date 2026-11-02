import { API } from "../config";
import type { CursorPage, FaceRead } from "../types";
import { startOperation, readMutationResponse, DefinitiveMutationError, type MutationReceipt, type OperationPatch } from "../stores/optimisticOperations";
import { entityKey, queryKey } from "../stores/queryKeys";

export interface FaceReviewGroup {
  id: number;
  face_ids: number[];
  size: number;
  media_count: number;
  mean_cos: number;
  min_cos: number;
  representative_face: FaceRead;
  member_faces: FaceRead[];
  candidate: { person_id: number; person_name: string; mean_cos: number; margin: number; runner_up_person_cos: number } | null;
}
export const ignoredFacesKey = queryKey("orphan-faces-ignored");
export const faceGroupsKey = queryKey("orphan-face-groups");
export const remainingGroupFaces = (ids: readonly number[], removed: readonly number[]) => ids.filter(id => !removed.includes(id));
export const appliedFaceIds = (receipt: MutationReceipt) => receipt.applied.filter(key => /^face:\d+$/.test(key)).map(key => Number(key.slice(5)));
export async function getIgnoredFaces(cursor: string | null = null): Promise<CursorPage<FaceRead>> {
  const params = new URLSearchParams({ limit: "48" });
  if (cursor) params.set("cursor", cursor);
  const response = await fetch(`${API}/api/faces/ignored?${params}`);
  if (!response.ok) throw new Error("Failed to load ignored faces");
  return response.json();
}
export async function getFaceGroups(cursor: string | null = null): Promise<CursorPage<FaceReviewGroup>> {
  const params = new URLSearchParams({ limit: "48" });
  if (cursor) params.set("cursor", cursor);
  const response = await fetch(`${API}/api/faces/orphans/groups?${params}`);
  if (!response.ok) throw new Error("Failed to load look-alike groups");
  const page: CursorPage<Omit<FaceReviewGroup, "id">> = await response.json();
  return { ...page, items: page.items.map(group => ({ ...group, id: group.representative_face.id })) };
}
export async function lookupFaceOperation(id: string): Promise<unknown> {
  const response = await fetch(`${API}/api/faces/operations/${encodeURIComponent(id)}`);
  if (!response.ok) throw new Error("Face operation status unavailable");
  return response.json();
}
export function startFaceReview(action: "ignore" | "unignore" | "accept", faceIds: readonly number[], target: { person_id?: number; new_person_name?: string; select_all?: boolean } = {}, faces: readonly FaceRead[] = [], groupId?: number) {
  const keys = [...new Set(faceIds)].map(id => entityKey("face", id));
  const patches: OperationPatch[] = keys.map(key => ({ kind: "remove", key, ...(action === "unignore" ? { query: ignoredFacesKey } : {}) }));
  if (action === "ignore" || action === "unignore") {
    for (const face of faces) patches.push({ kind: "insert", key: entityKey("face", face.id), query: action === "ignore" ? ignoredFacesKey : "orphan-faces", item: face });
  }
  if (groupId !== undefined) patches.push({ kind: "remove", key: entityKey("face", groupId), query: faceGroupsKey });
  return startOperation({
    label: action === "unignore" ? "Restore faces" : action === "ignore" ? "Ignore faces" : "Assign group",
    input: { face_ids: [...new Set(faceIds)], ...target }, resources: keys, patches,
    request: async (input, operation_id) => {
      const response = await fetch(`${API}/api/faces/${action === "accept" ? "orphans/groups/accept" : action}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...input, ...(input.select_all ? { face_ids: undefined } : {}), operation_id }),
      });
      return readMutationResponse(response, operation_id, lookupFaceOperation);
    },
    receipt: result => result,
    lookup: lookupFaceOperation,
    events: () => [{ type: "list:invalidate", prefix: "orphan-face" }, { type: "person:changed", ids: [] }],
  });
}

/** Ordinary mutations use durable receipts; remaining review endpoints retain their adapters. */
export function startLegacyFaceReview<R>(label: string, faceIds: readonly number[], path: string, body: Record<string, unknown>, options: { method?: string; query?: string; receipt?: (result: R, id: string) => MutationReceipt; onResult?: (result: R) => void } = {}) {
  const keys = [...new Set(faceIds)].map(id => entityKey("face", id));
  const id = crypto.randomUUID();
  return startOperation({
    id, label, input: { ...body }, resources: keys,
    patches: keys.map(key => ({ kind: "remove" as const, key, ...(options.query ? { query: options.query } : {}) })),
    request: async (input, operation_id) => {
      const method = options.method ?? "POST";
      const response = await fetch(`${API}/api/faces/${path}${path.includes("?") ? "&" : "?"}operation_id=${encodeURIComponent(operation_id)}`, {
        method, headers: { "Content-Type": "application/json" },
        ...(method === "DELETE" ? {} : { body: JSON.stringify({ ...input, operation_id }) }),
      });
      if (!response.ok) {
        // Validation/auth failures occur before commit; server/transport failures
        // can follow commits and must retain uncertain overlays.
        if ([400, 403, 404, 422].includes(response.status)) throw new DefinitiveMutationError(`${label} failed (${response.status})`);
        throw new Error(`${label} outcome uncertain (${response.status})`);
      }
      const result = await response.json() as R;
      options.onResult?.(result);
      return result;
    },
    ...(["assign", "detach", "create_person"].includes(path) || path.startsWith("?") ? {lookup: lookupFaceOperation} : {}),
    receipt: (result: R) => result && typeof result === "object" && "receipt" in result ? result.receipt : ["assign", "detach", "create_person"].includes(path) || path.startsWith("?") ? undefined : options.receipt ? options.receipt(result, id) : { operation_id: id, state: "succeeded", applied: keys, skipped: [], failed: [], unknown: [] },
    events: () => [{ type: "list:invalidate", prefix: "orphan-face" }, { type: "person:changed", ids: [] }],
  });
}
