import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useSearchParams } from "react-router-dom";
import { API } from "../config";
import type { Face, Person } from "../types";
import { useUndo } from "../context/UndoContext";
import { DefinitiveMutationError, optimisticOperations, projectEntity, reconcileOperation, type MutationReceipt } from "../stores/optimisticOperations";
import { startAvatarAuto, startAvatarChange } from "../services/personAvatarOptimistic";

async function saveAvatar(personId: number, face: Face | null) {
  const query = face ? `?face_id=${face.id}` : "";
  const response = await fetch(`${API}/api/person/${personId}/profile_face${query}`, { method: "POST" });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const message = body.detail || "Could not update profile photo";
    if (response.status < 500) throw new DefinitiveMutationError(message);
    throw new Error(message);
  }
}

async function avatarLookup(personId: number, expected: number | null, operationId: string): Promise<MutationReceipt> {
  const response = await fetch(`${API}/api/person/${personId}`);
  if (!response.ok) throw new Error("Could not check the profile photo. Try checking again.");
  const current: Person = await response.json();
  const applied = (current.profile_face?.id ?? null) === expected;
  return { operation_id: operationId, state: applied ? "succeeded" : "unknown", applied: applied ? [`person:${personId}`] : [], unknown: applied ? [] : [`person:${personId}`], skipped: [], failed: [] };
}

/** Avatar and URL state live with the hero; core overlays survive dialog closure. */
export function usePersonAvatar(base: Person) {
  const operations = useSyncExternalStore(optimisticOperations.subscribe, optimisticOperations.getSnapshot);
  const uncertain = operations.find(operation => operation.label === "Update profile photo" && operation.resources.includes(`person:${base.id}`) && operation.state === "unknown");
  const person = projectEntity(`person:${base.id}`, base, `person-detail:${base.id}`) ?? base;
  const [params, setParams] = useSearchParams();
  const open = params.get("avatar") === "1";
  const setOpen = (value: boolean) => setParams(previous => { const next = new URLSearchParams(previous); if (value) next.set("avatar", "1"); else next.delete("avatar"); return next; }, { replace: true });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = useRef(false);
  const { push } = useUndo();
  const previousFaces = useRef(new Map<string, { personId: number; face: Face | null }>());
  useEffect(() => {
    for (const operation of operations) {
      const previous = previousFaces.current.get(operation.id);
      if (!previous) continue;
      if (operation.state === "rejected") { previousFaces.current.delete(operation.id); continue; }
      if (operation.state !== "committed") continue;
      previousFaces.current.delete(operation.id);
      push({ label: "Profile photo updated", operationId: operation.id, undo: async () => {
        const restored = await startAvatarChange(previous.personId, previous.face,
          () => saveAvatar(previous.personId, previous.face),
          operationId => avatarLookup(previous.personId, previous.face?.id ?? null, operationId)).done;
        if (restored.state !== "committed") throw new Error(restored.error || "Could not confirm restoration. Check profile photo status before changing it again.");
      } });
    }
  }, [operations, push]);
  const change = async (face: Face | null) => {
    if (active.current || uncertain) return;
    active.current = true; setBusy(true); setError("");
    const previous = person.profile_face ?? null;
    try {
      const operation = startAvatarChange(person.id, face, () => saveAvatar(person.id, face), operationId => avatarLookup(person.id, face?.id ?? null, operationId));
      previousFaces.current.set(operation.id, { personId: person.id, face: previous });
      const result = await operation.done;
      if (result.state !== "committed") {
        setError(result.state === "unknown" ? "Profile photo update could not be confirmed. Check status before changing it again." : result.error || "Could not update profile photo. Try again.");
        return;
      }
      setOpen(false);

    } finally { active.current = false; setBusy(false); }
  };
  const autoPick = async () => {
    if (active.current || uncertain) return;
    active.current = true; setBusy(true); setError("");
    const previous = person.profile_face ?? null;
    try {
      let expected: number | null | undefined;
      const operation = startAvatarAuto(person.id, async () => {
        const response = await fetch(`${API}/api/person/${person.id}/profile_face/auto`, { method: "POST" });
        if (!response.ok) {
          const message = "Could not automatically select a profile photo. Try again.";
          if (response.status < 500) throw new DefinitiveMutationError(message);
          throw new Error(message);
        }
        const updated: Person = await response.json();
        expected = updated.profile_face?.id ?? null;
        return updated;
      }, async operationId => {
        if (expected === undefined) throw new Error("Automatic selection could not be confirmed. Refresh to inspect the current photo; do not repeat the request.");
        return avatarLookup(person.id, expected, operationId);
      });
      previousFaces.current.set(operation.id, { personId: person.id, face: previous });
      const result = await operation.done;
      if (result.state === "committed") setOpen(false);
      else setError(result.state === "unknown" ? "Automatic profile selection could not be confirmed. Check status before changing it again." : result.error || "Could not select a profile photo.");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not select a profile photo"); }
    finally { active.current = false; setBusy(false); }
  };
  const checkStatus = async () => {
    if (!uncertain || active.current) return;
    active.current = true; setBusy(true);
    try {
      const result = await reconcileOperation(uncertain.id);
      setError(result.state === "committed" ? "" : "The server has not confirmed this profile photo yet. Check status again before making another change.");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not check profile photo status"); }
    finally { active.current = false; setBusy(false); }
  };
  return { person, uncertain: Boolean(uncertain), checkStatus, open, setOpen, busy, error, clearError: () => setError(""), change, autoPick };
}
