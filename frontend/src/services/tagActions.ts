import { mutationBus } from "../stores/mutationBus";
import { apiFetch } from "./api";

export const removeTagFromMedia = async (mediaId: number, tagId: number) =>
  apiFetch<void>(`/api/tags/media/${mediaId}/${tagId}`, {
    method: "DELETE",
    errorMessage: "Failed to remove tag from media",
  }).then((result) => {
    mutationBus.emit({ type: "tag:changed", ids: [tagId] });
    mutationBus.emit({ type: "list:invalidate", prefix: "" });
    return result;
  });

export const removeTagFromPerson = async (personId: number, tagId: number) =>
  apiFetch<void>(`/api/tags/person/${personId}/${tagId}`, {
    method: "DELETE",
    errorMessage: "Failed to remove tag from person",
  }).then((result) => {
    mutationBus.emit({ type: "tag:changed", ids: [tagId] });
    mutationBus.emit({ type: "list:invalidate", prefix: "" });
    return result;
  });

export const deleteTag = async (tagId: number) =>
  apiFetch<void>(`/api/tags/${tagId}`, {
    method: "DELETE",
    errorMessage: "Failed to delete tag",
  }).then((result) => {
    mutationBus.emit({ type: "tag:changed", ids: [tagId] });
    mutationBus.emit({ type: "list:invalidate", prefix: "" });
    return result;
  });
