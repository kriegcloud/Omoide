import { API } from "../config";
import { Media, MediaPreview, Task } from "../types";
import type { MediaDetail } from "../types";
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
};

export const deleteMediaFile = async (mediaId: number) => {
  const res = await fetch(`${API}/api/media/${mediaId}/file`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error("Failed to delete media file");
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
  return res.json();
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
  return res.json();
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
  return res.json();
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
  return res.json();
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
  return res.json();
};
