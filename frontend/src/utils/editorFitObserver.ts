import { refitEditorState, type FilerobotFitState } from "./editorOps";

/** Observe provider fits without recording edits or applying obsolete snapshots. */
export function observeEditorFits(
  read: () => Promise<FilerobotFitState | null>,
  apply: (update: (current: FilerobotFitState) => Partial<FilerobotFitState> | null) => void,
  schedule: (callback: FrameRequestCallback) => number,
  cancel: (frame: number) => void,
): () => void {
  let previous: FilerobotFitState | null = null;
  let active = true;
  let revision = 0;
  let pending: { revision: number; basis: FilerobotFitState } | null = null;
  let frame: number;
  const observe = async () => {
    const snapshot = await read();
    if (!active) return;
    if (snapshot) {
      if (pending) {
        if (snapshot.omoideFitRevision === pending.revision) previous = pending.basis;
        // A non-null read fences the queued reducer. A timed-out/null read
        // cannot establish acceptance or rejection, so retain pending state.
        pending = null;
      }
      const patch = refitEditorState(previous, snapshot);
      if (patch) {
        // This private store marker survives user edits/Undo, but is excluded
        // from serialized designs. A read queues behind UPDATE_STATE and acts
        // as a fence: only acknowledge a correction actually accepted by the
        // provider reducer, never a merely scheduled callback.
        revision = Math.max(revision, snapshot.omoideFitRevision ?? 0) + 1;
        const correction = { ...patch, omoideFitRevision: revision };
        pending = { revision, basis: { ...snapshot, ...correction } };
        apply(current => active &&
          current.adjustments === snapshot.adjustments &&
          current.shownImageDimensions === snapshot.shownImageDimensions &&
          current.pastDesignStates === snapshot.pastDesignStates &&
          current.futureDesignStates === snapshot.futureDesignStates
            ? correction : null);
        const settled = await read();
        if (!active) return;
        if (settled) {
          if (settled.omoideFitRevision === revision) previous = pending.basis;
          pending = null;
        }
        // On rejection retain the old coordinate basis for the next read.
      } else previous = snapshot;
    }
    frame = schedule(() => { void observe(); });
  };
  frame = schedule(() => { void observe(); });
  return () => { active = false; cancel(frame); };
}
