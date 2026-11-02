import { DefinitiveMutationError } from "../stores/optimisticOperations";
import { mutationBus } from "../stores/mutationBus";
import { apiFetch } from "./api";

export const removeTagFromMedia = async (mediaId: number, tagId: number, operationId?: string) => {
  await apiFetch<void>(`/api/tags/media/${mediaId}/${tagId}`, {
    method: "DELETE", query: operationId ? {operation_id: operationId} : undefined,
    errorMessage: "Failed to remove tag from media",
  }).catch(rejectValidation);
  mutationBus.emit({ type: "tag:changed", ids: [tagId] });
  mutationBus.emit({ type: "list:invalidate", prefix: "" });
  return operationId ? lookupMediaTagOperation(operationId) : undefined;
};

export const lookupMediaTagOperation = (id: string) => apiFetch(`/api/media/operations/${id}`);

export const removeTagFromPerson = async (personId: number, tagId: number) =>
  apiFetch<void>(`/api/tags/person/${personId}/${tagId}`, {
    method: "DELETE",
    errorMessage: "Failed to remove tag from person",
  }).catch(rejectValidation).then((result) => {
    mutationBus.emit({ type: "tag:changed", ids: [tagId] });
    mutationBus.emit({ type: "list:invalidate", prefix: "" });
    return result;
  });

export const deleteTag = async (tagId: number) =>
  apiFetch<void>(`/api/tags/${tagId}`, {
    method: "DELETE",
    errorMessage: "Failed to delete tag",
  }).catch(rejectValidation).then((result) => {
    mutationBus.emit({ type: "tag:changed", ids: [tagId] });
    mutationBus.emit({ type: "list:invalidate", prefix: "" });
    return result;
  });

/** These routes reject validation/permission/missing targets before any write.
 * Commit/transport errors (including 409/500) remain uncertain. */
function rejectValidation(error: unknown): never {
  if (error instanceof Error && "status" in error && [400, 403, 404, 422].includes(Number(error.status))) {
    throw new DefinitiveMutationError(error.message);
  }
  throw error;
}
