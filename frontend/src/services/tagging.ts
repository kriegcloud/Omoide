import { mutationBus } from "../stores/mutationBus";
import { Tag } from "../types";
import { apiFetch } from "./api";

export const createTag = async (name: string): Promise<Tag> =>
  apiFetch<Tag>("/api/tags/", {
    method: "POST",
    body: { name },
    errorMessage: "Failed to create tag",
  }).then((result) => {
    mutationBus.emit({ type: "tag:changed", ids: [result.id] });
    return result;
  });

export const assignTag = async (ownerType: "media" | "person", ownerId: number, tagId: number) =>
  apiFetch<void>(`/api/tags/${ownerType}/${ownerId}/${tagId}`, {
    method: "POST",
    errorMessage: "Failed to assign tag",
  }).then((result) => {
    mutationBus.emit({ type: "tag:changed", ids: [tagId] });
    mutationBus.emit({ type: "list:invalidate", prefix: "" });
    return result;
  });
