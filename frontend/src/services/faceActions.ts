import { startOperation, type MutationReceipt, DefinitiveMutationError, revalidateQueries, revalidateUnknownQueries } from "../stores/optimisticOperations";
import { mutationBus } from "../stores/mutationBus";
import { API } from "../config";
import { FaceAssignSource, Person } from "../types";

const faceMutationError = (response: Response, message: string) =>
  [403, 404, 422].includes(response.status) ? new DefinitiveMutationError(message) : new Error(message);

// Ordinary face writes return durable, exact member outcomes when opted in.
export const assignFace = async (
  faceIds: number[],
  personId: number,
  source: FaceAssignSource = "manual",
  invalidationPrefix = "",
  operationId: string = crypto.randomUUID(),
) => {
  const body = { face_ids: faceIds, person_id: personId, source, operation_id: operationId };
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
  return res.json();
};

export const createPersonFromFaces = async (
  faceIds: number[],
  name?: string,
  operationId: string = crypto.randomUUID(),
): Promise<Person> => {
  const res = await fetch(`${API}/api/faces/create_person`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ face_ids: faceIds, name: name, operation_id: operationId }),
  });
  if (!res.ok) throw new Error(`Create person failed: ${res.status}`);
  const json = await res.json();
  const person: Person = json.person ?? json;
  mutationBus.emit({ type: "person:created", id: person.id });
  mutationBus.emit({ type: "person:changed", ids: [] });
  mutationBus.emit({ type: "list:invalidate", prefix: "" });
  return person;
};

export const deleteFace = async (faceIds: number[], invalidationPrefix = "", operationId: string = crypto.randomUUID()) => {
  const params = new URLSearchParams({ operation_id: operationId });
  faceIds.forEach((id) => params.append("face_ids", id.toString()));
  const res = await fetch(`${API}/api/faces/?${params.toString()}`, {
    method: "DELETE",
  });
  if (!res.ok) throw faceMutationError(res, `Delete failed: ${res.status}`);
  mutationBus.emit({ type: "list:invalidate", prefix: invalidationPrefix });
  mutationBus.emit({ type: "person:changed", ids: [] });
  return res.json();
};

export const detachFace = async (faceIds: number[], invalidationPrefix = "", operationId: string = crypto.randomUUID()) => {
  const res = await fetch(`${API}/api/faces/detach`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ face_ids: faceIds, operation_id: operationId }),
  });
  if (!res.ok) throw faceMutationError(res, `Detach failed: ${res.status}`);
  mutationBus.emit({ type: "list:invalidate", prefix: invalidationPrefix });
  mutationBus.emit({ type: "person:changed", ids: [] });
  return res.json();
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

/** One identity binds optimistic intent, transport, and durable recovery. */
export function startFaceMutation(label: string, faceIds: readonly number[], request: (operationId: string) => Promise<unknown>, query: string, personIds: readonly number[] = [], options: { rollbackOnError?: boolean; onError?: (error: unknown) => void } = {}) {
  const keys = [...new Set(faceIds)].map(id => `face:${id}` as const);
  const id = crypto.randomUUID();
  return startOperation({
    id, label, input: { faceIds: [...faceIds] },
    resources: [...keys, ...personIds.map(id => `person:${id}` as const)],
    patches: keys.map(key => ({ kind: "remove" as const, key, query })),
    request: () => request(id),
    lookup: lookupFaceOperation,
    onError: options.onError,
    rollbackOnError: options.rollbackOnError,
    reconcileUnknownAfterRefresh: false,
    refresh: () => revalidateQueries([query]),
    refreshUnknown: () => revalidateUnknownQueries([query]),
    responsePatches: () => [],
    receipt: result => (result as { receipt?: unknown } | undefined)?.receipt,
  });
}

export async function lookupFaceOperation(id: string) {
  const response = await fetch(`${API}/api/faces/operations/${id}`);
  if (!response.ok) throw new Error(`Face operation lookup failed: ${response.status}`);
  return response.json();
}

/** Construct inverses only from committed members and their recorded ownership. */
export async function restoreFaceOwnership(receipt: MutationReceipt): Promise<void> {
  const groups = new Map<number | null, number[]>();
  for (const key of receipt.applied) {
    if (!/^face:\d+$/.test(key)) continue;
    if (!receipt.previous_ownership || !(key in receipt.previous_ownership)) throw new Error("Previous face ownership unavailable");
    const previous = receipt.previous_ownership[key];
    const ids = groups.get(previous) ?? [];
    ids.push(Number(key.slice(5))); groups.set(previous, ids);
  }
  for (const [previous, ids] of groups) {
    const operation = startFaceMutation("Restore face ownership", ids,
      id => previous === null ? detachFace(ids, "orphan-face", id) : assignFace(ids, previous, "undo", "orphan-face", id),
      "", previous === null ? [] : [previous]);
    const record = await operation.done;
    if (record.state !== "committed") throw new Error("Some face ownership could not be restored; check operation status");
  }
}

/** Promise adapter for secondary face editors; the global command survives unmount. */
export async function runFaceCommand<R>(label: string, ids: number[], request: (operationId: string) => Promise<R>): Promise<R> {
  let result!: R;
  const handle = startFaceMutation(label, ids, async id => { result = await request(id); return result; }, "");
  const record = await handle.done;
  if (record.state === "unknown" || record.state === "rejected") throw new Error(record.error ?? "Face outcome uncertain; check operation status");
  return result;
}
