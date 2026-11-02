import { startOperation, DefinitiveMutationError, revalidateQueries, revalidateUnknownQueries } from "../stores/optimisticOperations";
import { mutationBus } from "../stores/mutationBus";
import { API } from "../config";
import { FaceAssign, FaceAssignSource, Person } from "../types";

const faceMutationError = (response: Response, message: string) =>
  [403, 404, 422].includes(response.status) ? new DefinitiveMutationError(message) : new Error(message);

// These endpoints skip missing faces and return no committed face IDs.
// Refresh authoritative membership instead of claiming the requested subset.
export const assignFace = async (
  faceIds: number[],
  personId: number,
  source: FaceAssignSource = "manual",
  invalidationPrefix = "",
) => {
  const body: FaceAssign = { face_ids: faceIds, person_id: personId, source };
  const res = await fetch(`${API}/api/faces/assign`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    if ([403, 404, 422].includes(res.status)) throw new DefinitiveMutationError(`Assign rejected: ${res.status}`);
    throw new Error(`Assign outcome uncertain: ${res.status}`);
  }
  mutationBus.emit({ type: "list:invalidate", prefix: invalidationPrefix });
  mutationBus.emit({ type: "person:changed", ids: [] });
};

export const createPersonFromFaces = async (
  faceIds: number[],
  name?: string
): Promise<Person> => {
  const res = await fetch(`${API}/api/faces/create_person`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ face_ids: faceIds, name: name }),
  });
  if (!res.ok) throw new Error(`Create person failed: ${res.status}`);
  const json = await res.json();
  const person: Person = json.person ?? json;
  mutationBus.emit({ type: "person:created", id: person.id });
  mutationBus.emit({ type: "person:changed", ids: [] });
  mutationBus.emit({ type: "list:invalidate", prefix: "" });
  return person;
};

export const deleteFace = async (faceIds: number[], invalidationPrefix = "") => {
  const params = new URLSearchParams();
  faceIds.forEach((id) => params.append("face_ids", id.toString()));
  const res = await fetch(`${API}/api/faces/?${params.toString()}`, {
    method: "DELETE",
  });
  if (!res.ok) throw faceMutationError(res, `Delete failed: ${res.status}`);
  mutationBus.emit({ type: "list:invalidate", prefix: invalidationPrefix });
  mutationBus.emit({ type: "person:changed", ids: [] });
};

export const detachFace = async (faceIds: number[], invalidationPrefix = "") => {
  const res = await fetch(`${API}/api/faces/detach`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ face_ids: faceIds }),
  });
  if (!res.ok) throw faceMutationError(res, `Detach failed: ${res.status}`);
  mutationBus.emit({ type: "list:invalidate", prefix: invalidationPrefix });
  mutationBus.emit({ type: "person:changed", ids: [] });
};

export const deleteAllOrphanFaces = async (): Promise<{ deleted: number }> => {
  const response = await fetch(`${API}/api/faces/orphans`, { method: "DELETE" });
  if (!response.ok) {
    const error: { detail?: unknown } = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === "string" ? error.detail : "Failed to delete all unassigned faces");
  }
  const result = await response.json();
  mutationBus.emit({ type: "list:invalidate", prefix: "orphan-face" });
  mutationBus.emit({ type: "list:invalidate", prefix: "orphan-faces" });
  return result;
};

export const rejectFaceSuggestion = async (faceId: number, personId: number): Promise<void> => {
  const response = await fetch(`${API}/api/faces/${faceId}/reject-suggestion`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ person_id: personId }),
  });
  if (!response.ok) throw new Error(`Failed to reject suggestion (${response.status})`);
  mutationBus.emit({ type: "list:invalidate", prefix: "orphan-face-suggestions" });
};

/** Message-only legacy endpoints acknowledge the command, not individual faces.
 * Never manufacture applied IDs or an inverse from requested ownership. */
export function startFaceMutation(label: string, faceIds: readonly number[], request: () => Promise<unknown>, query: string, personIds: readonly number[] = [], options: { rollbackOnError?: boolean; onError?: (error: unknown) => void } = {}) {
  const keys = [...new Set(faceIds)].map(id => `face:${id}` as const);
  const id = crypto.randomUUID();
  return startOperation({
    id, label, input: { faceIds: [...faceIds] },
    resources: [...keys, ...personIds.map(id => `person:${id}` as const)],
    patches: keys.map(key => ({ kind: "remove" as const, key, query })),
    request,
    onError: options.onError,
    rollbackOnError: options.rollbackOnError,
    reconcileUnknownAfterRefresh: true,
    refresh: () => revalidateQueries([query]),
    refreshUnknown: () => revalidateUnknownQueries([query]),
    responsePatches: () => [],
    receipt: () => ({ operation_id: id, state: "succeeded", applied: [], skipped: [], failed: [], unknown: [] }),
  });
}
