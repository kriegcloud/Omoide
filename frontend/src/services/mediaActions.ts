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

export const convertMedia = async (mediaId: number): Promise<Task> => {
  const res = await fetch(`${API}/api/media/${mediaId}/converter`, {
    method: "POST",
  });
  if (!res.ok) throw new Error("Failed to start conversion");
  return res.json();
};

export const deleteMediaRecord = async (mediaId: number) => {
  const res = await fetch(`${API}/api/media/${mediaId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error("Failed to delete media record");
  mutationBus.emit({ type: "media:deleted", ids: [mediaId] });
};

export const deleteMediaFile = async (mediaId: number) => {
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

export const setMediaFavorite = async (
  mediaId: number,
  isFavorite: boolean
): Promise<Media> => {
  const res = await fetch(`${API}/api/media/${mediaId}/favorite`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ is_favorite: isFavorite }),
  });
  if (!res.ok) throw new Error("Failed to update favorite");
  const result = await res.json();
  mutationBus.emit({ type: "media:updated", items: [result] });
  return result;
};

export const moveMedia = async (
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

export const renameMedia = async (
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

export const bulkMoveMedia = async (
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
  return res.json();
};

export const editMedia = async (
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

export const batchEditMedia = async (
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

export const bulkDeleteMedia = async (ids: number[], action: BulkDeleteAction): Promise<BulkDeleteResult> => {
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
