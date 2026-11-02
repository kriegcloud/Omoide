import { DefinitiveMutationError } from "../stores/optimisticOperations";
import { mutationBus } from "../stores/mutationBus";
import { Tag } from "../types";
import { apiFetch } from "./api";

export const createTag = async (name: string): Promise<Tag> =>
  apiFetch<Tag>("/api/tags/", {
    method: "POST",
    body: { name },
    errorMessage: "Failed to create tag",
  }).catch(rejectValidation).then((result) => {
    mutationBus.emit({ type: "tag:changed", ids: [result.id] });
    return result;
  });

export const assignTag = async (ownerType: "media" | "person", ownerId: number, tagId: number, operationId?: string) => {
  await apiFetch<void>(`/api/tags/${ownerType}/${ownerId}/${tagId}`, {
    method: "POST", query: operationId ? {operation_id: operationId} : undefined,
    errorMessage: "Failed to assign tag",
  }).catch(rejectValidation);
  mutationBus.emit({ type: "tag:changed", ids: [tagId] });
  mutationBus.emit({ type: "list:invalidate", prefix: "" });
  return operationId ? lookupMediaTagOperation(operationId) : undefined;
};

export const lookupMediaTagOperation = (id: string) => apiFetch(`/api/media/operations/${id}`);


/** These routes reject validation/permission/missing targets before any write.
 * Commit/transport errors (including 409/500) remain uncertain. */
function rejectValidation(error: unknown): never {
  if (error instanceof Error && "status" in error && [400, 403, 404, 422].includes(Number(error.status))) {
    throw new DefinitiveMutationError(error.message);
  }
  throw error;
}
