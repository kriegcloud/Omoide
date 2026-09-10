import type { MediaPreview } from "../types";

export type MediaPatch = { id: number; latitude?: number; longitude?: number } & Partial<MediaPreview>;
export type MutationEvent =
  | { type: "media:deleted"; ids: number[] }
  | { type: "media:updated"; items: MediaPatch[] }
  | { type: "media:moved"; items: MediaPatch[]; fromFolder: string | null; toFolder: string }
  | { type: "face:deleted"; ids: number[] }
  | { type: "face:assigned"; faceIds: number[]; personId: number; previous: Record<number, number | null> }
  | { type: "face:detached"; faceIds: number[]; personId: number | null }
  | { type: "person:changed"; ids: number[] }
  | { type: "person:created"; id: number }
  | { type: "duplicates:resolved"; groupId: number; mediaIds: number[] }
  | { type: "list:invalidate"; prefix: string };
export type MutationType = MutationEvent["type"];
const listeners = new Set<(event: MutationEvent) => void>();
/** Services emit after success; stores subscribe. A view failure cannot fail a committed mutation. */
export const mutationBus = {
  emit(event: MutationEvent) {
    for (const listener of [...listeners]) {
      try { listener(event); } catch (error) { console.error("Mutation subscriber failed", error); }
    }
  },
  subscribe(listener: (event: MutationEvent) => void) {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  },
};

/** The caller owns local presentation and reports the rejected request in its snackbar. */
export async function runOptimistic<Snapshot, Result>({ apply, request, rollback }: {
  apply: () => Snapshot;
  request: () => Promise<Result>;
  rollback: (snapshot: Snapshot) => void;
}): Promise<Result> {
  const snapshot = apply();
  try { return await request(); } catch (error) { rollback(snapshot); throw error; }
}
