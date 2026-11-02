import { mutationBus } from "../stores/mutationBus";
import { SceneRead } from "../types";
import { apiFetch } from "./api";

export const getScenes = async (
  mediaId: number,
  signal?: AbortSignal
): Promise<SceneRead[]> =>
  apiFetch<SceneRead[]>(`/api/media/${mediaId}/scenes`, {
    errorMessage: "Failed to fetch scenes",
    signal,
  });

export const createScene = async (
  mediaId: number,
  data: { start_time: number; end_time?: number; description?: string }
): Promise<SceneRead> =>
  apiFetch<SceneRead>(`/api/media/${mediaId}/scenes`, {
    method: "POST",
    body: data,
    errorMessage: "Failed to create scene",
  }).then((result) => {
    mutationBus.emit({ type: "media:updated", items: [{ id: mediaId }] });
    mutationBus.emit({ type: "list:invalidate", prefix: "" });
    return result;
  });

export const deleteScene = async (
  mediaId: number,
  sceneId: number
): Promise<void> =>
  apiFetch<void>(`/api/media/${mediaId}/scenes/${sceneId}`, {
    method: "DELETE",
    errorMessage: "Failed to delete scene",
  }).then((result) => {
    mutationBus.emit({ type: "media:updated", items: [{ id: mediaId }] });
    mutationBus.emit({ type: "list:invalidate", prefix: "" });
    return result;
  });
