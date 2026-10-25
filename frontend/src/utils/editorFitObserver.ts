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
  let frame: number;
  const observe = async () => {
    const snapshot = await read();
    if (!active) return;
    if (snapshot) {
      const patch = refitEditorState(previous, snapshot);
      previous = { ...snapshot, ...patch };
      if (patch) {
        apply(current => active &&
          current.adjustments === snapshot.adjustments &&
          current.shownImageDimensions === snapshot.shownImageDimensions &&
          current.pastDesignStates === snapshot.pastDesignStates &&
          current.futureDesignStates === snapshot.futureDesignStates
            ? patch : null);
      }
    }
    frame = schedule(() => { void observe(); });
  };
  frame = schedule(() => { void observe(); });
  return () => { active = false; cancel(frame); };
}
