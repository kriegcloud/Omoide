import { startOperation } from "../stores/optimisticOperations";
import type { Face, Person } from "../types";

/** Shared avatar intent: both fields change together and core owns rollback. */
export function startAvatarChange(personId: number, face: Face | null, request: () => Promise<unknown>, lookup?: (operationId: string) => Promise<unknown>, refreshUnknown?: () => Promise<void>) {
  return startOperation({
    label: "Update profile photo",
    input: { personId, faceId: face?.id ?? null },
    resources: [`person:${personId}`],
    patches: [{ kind: "fields", key: `person:${personId}`, values: { profile_face: face, profile_face_id: face?.id ?? null } }],
    request,
    lookup: refreshUnknown ? undefined : lookup,
    refreshUnknown, reconcileUnknownAfterRefresh: Boolean(refreshUnknown),
    events: () => [{ type: "person:changed", ids: [personId] }],
  });
}

/** Auto selection has no known face until the server returns its chosen person. */
export function startAvatarAuto(personId: number, request: () => Promise<Person>, lookup?: (operationId: string) => Promise<unknown>, refreshUnknown?: () => Promise<void>) {
  return startOperation({
    label: "Update profile photo",
    input: { personId },
    resources: [`person:${personId}`],
    patches: [{ kind: "derived", key: `person:${personId}`, field: "profile_face" }],
    request,
    lookup: refreshUnknown ? undefined : lookup,
    refreshUnknown, reconcileUnknownAfterRefresh: Boolean(refreshUnknown),
    responsePatches: result => [{ kind: "fields", key: `person:${personId}`, values: { profile_face: result.profile_face ?? null, profile_face_id: result.profile_face?.id ?? null } }],
    events: () => [{ type: "person:changed", ids: [personId] }],
  });
}
