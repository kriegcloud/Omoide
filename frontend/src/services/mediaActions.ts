import { DefinitiveMutationError, startOperation, type MutationReceipt, type OperationPatch } from "../stores/optimisticOperations";
import { entityKey } from "../stores/queryKeys";
import { mutationBus } from "../stores/mutationBus";
import { API } from "../config";
import { Media, MediaPreview, Task } from "../types";
import type { CropAspect, CropFraming, FaceCropSuggestion, MediaDetail } from "../types";
import type { EditOp, FilerobotDesignState } from "../utils/editorOps";

export interface BulkMoveResult {
  moved_ids: number[];
  skipped: { id: number; reason: string }[];
}

export interface EditMediaRequest {
  ops: EditOp[];
  mode: "copy" | "overwrite";
  design_state: FilerobotDesignState | null;
}

const responseError = async (res: Response, fallback: string) => {
  const data = await res.json().catch(() => null);
  return new Error(data?.detail || fallback);
};

export const convertMedia = async (mediaId: number, mode: Exclude<import("../types").VideoConversionMode, "off">): Promise<Task> => {
  const res = await fetch(`${API}/api/media/${mediaId}/converter`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({mode}),
  });
  if (!res.ok) throw new Error("Failed to start conversion");
  return res.json();
};

const deleteMediaRecordRequest = async (mediaId: number) => {
  const res = await fetch(`${API}/api/media/${mediaId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error("Failed to delete media record");
  mutationBus.emit({ type: "media:deleted", ids: [mediaId] });
};

const deleteMediaFileRequest = async (mediaId: number) => {
  const res = await fetch(`${API}/api/media/${mediaId}/file`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error("Failed to delete media file");
  mutationBus.emit({ type: "media:deleted", ids: [mediaId] });
};

export const openMediaFolder = async (mediaId: number): Promise<void> => {
  const res = await fetch(`${API}/api/media/${mediaId}/open-folder`, {
    method: "POST",
  });
  if (!res.ok) {
    const msg = await res.text().catch(() => "Failed to open folder");
    throw new Error(msg || "Failed to open folder");
  }
};

export const openMediaFile = async (mediaId: number): Promise<void> => {
  const res = await fetch(`${API}/api/media/${mediaId}/open-file`, {
    method: "POST",
  });
  if (!res.ok) {
    const msg = await res.text().catch(() => "Failed to open file");
    throw new Error(msg || "Failed to open file");
  }
};

const setMediaFavoriteRequest = async (
  mediaId: number,
  isFavorite: boolean
): Promise<Media> => {
  const res = await fetch(`${API}/api/media/${mediaId}/favorite`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ is_favorite: isFavorite }),
  });
  if (!res.ok) {
    // Validation/presentation guards and a missing row precede the favorite write.
    if ([403, 404, 422].includes(res.status)) throw new DefinitiveMutationError("Failed to update favorite");
    throw new Error("Failed to update favorite");
  }
  const result = await res.json();
  mutationBus.emit({ type: "media:updated", items: [result] });
  return result;
};

const moveMediaRequest = async (
  mediaId: number,
  destinationDir: string
): Promise<MediaPreview> => {
  const res = await fetch(`${API}/api/media/${mediaId}/move`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ destination_dir: destinationDir }),
  });
  if (!res.ok) throw await responseError(res, "Failed to move media");
  const result = await res.json();
  mutationBus.emit({ type: "media:moved", items: [result], fromFolder: null, toFolder: destinationDir });
  return result;
};

const renameMediaRequest = async (
  mediaId: number,
  filename: string
): Promise<MediaPreview> => {
  const res = await fetch(`${API}/api/media/${mediaId}/rename`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename }),
  });
  if (!res.ok) throw await responseError(res, "Failed to rename media");
  const result = await res.json();
  mutationBus.emit({ type: "media:updated", items: [result] });
  return result;
};

const bulkMoveMediaRequest = async (
  mediaIds: number[],
  destinationDir: string
): Promise<BulkMoveResult> => {
  const res = await fetch(`${API}/api/media/bulk-move`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ media_ids: mediaIds, destination_dir: destinationDir }),
  });
  if (!res.ok) throw await responseError(res, "Failed to move selected media");
  const result = await res.json();
  mutationBus.emit({ type: "media:moved", items: result.moved_ids.map((id: number) => ({ id })), fromFolder: null, toFolder: destinationDir });
  return result;
};

export const createMediaFolder = async (
  parentPath: string,
  name: string
): Promise<{ path: string; name: string }> => {
  const res = await fetch(`${API}/api/media/folders`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parent_path: parentPath, name }),
  });
  if (!res.ok) throw await responseError(res, "Failed to create folder");
  const result = await res.json();
  mutationBus.emit({ type: "list:invalidate", prefix: "" });
  return result;
};

const editMediaRequest = async (
  mediaId: number,
  request: EditMediaRequest
): Promise<MediaDetail> => {
  const res = await fetch(`${API}/api/media/${mediaId}/edit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!res.ok) throw await responseError(res, "Failed to save image edits");
  const result = await res.json();
  if (request.mode === "overwrite") mutationBus.emit({ type: "media:updated", items: [result.media] });
  else mutationBus.emit({ type: "list:invalidate", prefix: "" });
  return result;
};

export const getFaceCropSuggestions = async (
  mediaId: number,
  framing: CropFraming = "portrait",
  aspect: CropAspect = "free",
  personId?: number | null
): Promise<FaceCropSuggestion[]> => {
  const params = new URLSearchParams({ framing, aspect });
  if (personId != null) params.set("person_id", String(personId));
  const res = await fetch(`${API}/api/media/${mediaId}/face-crops?${params}`);
  if (!res.ok) throw await responseError(res, "Failed to load face crop suggestions");
  return res.json();
};

const batchEditMediaRequest = async (
  mediaIds: number[],
  ops: EditOp[],
  mode: "copy" | "overwrite" = "copy"
): Promise<Task> => {
  const res = await fetch(`${API}/api/media/batch-edit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ media_ids: mediaIds, ops, mode }),
  });
  if (!res.ok) throw await responseError(res, "Failed to start batch edit");
  return res.json();
};

export type BulkDeleteAction = "DELETE_FILES" | "DELETE_RECORDS" | "BLACKLIST_RECORDS";
export interface BulkDeleteResult {
  removed: number;
  processed_ids: number[];
  skipped_ids: number[];
  errors: { id: number; error?: string; reason?: string }[];
}

const bulkDeleteMediaRequest = async (ids: number[], action: BulkDeleteAction): Promise<BulkDeleteResult> => {
  const res = await fetch(`${API}/api/media/bulk-delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ media_ids: ids, action }),
  });
  if (!res.ok) throw await responseError(res, "Failed to delete media");
  const result = await res.json();
  if (Array.isArray(result.processed_ids)) mutationBus.emit({ type: "media:deleted", ids: result.processed_ids });
  else mutationBus.emit({ type: "list:invalidate", prefix: "" });
  return result;
};

/** Keep the promise API for existing callers; the app-level controller owns lifetime. */
function mediaOperation<A extends unknown[], R>(
  label: string, ids: number[], args: A, request: (...args: A) => Promise<R>,
  patches: OperationPatch[] = [],
  outcome?: (result: R, id: string) => MutationReceipt,
): Promise<R> {
  let result: R;
  const id = crypto.randomUUID();
  const handle = startOperation({
    id, label, input: args, resources: ids.map(id => entityKey("media", id)), patches,
    request: async input => { result = await request(...input as A); return result; },
    ...(outcome ? { receipt: (value: R) => outcome(value, id) } : {}),
  });
  return handle.done.then(record => {
    if (record.state === "unknown" || record.state === "rejected") throw new Error(record.error ?? "Mutation outcome uncertain");
    return result;
  });
}
const removals = (ids: number[]): OperationPatch[] => ids.map(id => ({ kind: "remove", key: entityKey("media", id) }));
export const deleteMediaRecord = (id: number) => mediaOperation("Remove media record", [id], [id], deleteMediaRecordRequest, removals([id]));
export const deleteMediaFile = (id: number) => mediaOperation("Delete media file", [id], [id], deleteMediaFileRequest, removals([id]));
export const setMediaFavorite = (id: number, favorite: boolean) => mediaOperation(
  "Update favorite", [id], [id, favorite] as [number, boolean], setMediaFavoriteRequest,
  [{ kind: "fields", key: entityKey("media", id), values: { is_favorite: favorite } }],
);
export const moveMedia = (id: number, destination: string) => mediaOperation("Move media", [id], [id, destination] as [number, string], moveMediaRequest);
export const renameMedia = (id: number, filename: string) => mediaOperation("Rename media", [id], [id, filename] as [number, string], renameMediaRequest);
export const editMedia = (id: number, request: EditMediaRequest) => mediaOperation("Save image edits", [id], [id, request] as [number, EditMediaRequest], editMediaRequest);
// This operation tracks task submission only; task progress remains owned by TaskEventsContext.
export const batchEditMedia = (ids: number[], ops: EditOp[], mode: "copy" | "overwrite" = "copy") => mediaOperation(
  "Submit batch edit", ids, [ids, ops, mode] as [number[], EditOp[], "copy" | "overwrite"], batchEditMediaRequest,
);
export const bulkMoveMedia = (ids: number[], destination: string) => mediaOperation(
  "Move selected media", ids, [ids, destination] as [number[], string], bulkMoveMediaRequest, [],
  (result, operation_id) => ({ operation_id, state: result.skipped.length ? "partial" : "succeeded",
    applied: result.moved_ids.map(id => entityKey("media", id)),
    skipped: result.skipped.map(item => ({ key: entityKey("media", item.id), reason: item.reason })), failed: [], unknown: [] }),
);
export const bulkDeleteMedia = (mediaIds: number[], action: BulkDeleteAction) => {
  const ids = [...new Set(mediaIds)];
  return mediaOperation(
  "Delete selected media", ids, [ids, action] as [number[], BulkDeleteAction], bulkDeleteMediaRequest, removals(ids),
  (result, operation_id) => {
    const applied = result.processed_ids.map(id => entityKey("media", id));
    const failed = result.errors.map(item => ({ key: entityKey("media", item.id), code: "delete_failed", message: item.error ?? item.reason ?? "Delete failed" }));
    const failedKeys = new Set(failed.map(item => item.key));
    const skipped = result.skipped_ids.filter(id => !failedKeys.has(entityKey("media", id))).map(id => ({ key: entityKey("media", id), reason: "Skipped by server" }));
    const accounted = new Set([...applied, ...failed.map(item => item.key), ...skipped.map(item => item.key)]);
    const unknown = ids.map(id => entityKey("media", id)).filter(key => !accounted.has(key));
    return { operation_id, state: unknown.length ? "unknown" : failed.length || skipped.length ? "partial" : "succeeded", applied, failed, skipped, unknown };
  },
);
};
