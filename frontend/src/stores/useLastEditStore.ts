import { create } from "zustand";
import type { EditOp } from "../utils/editorOps";

interface LastEditStoreState {
  ops: EditOp[] | null;
  setLastEdit: (ops: EditOp[]) => void;
}

export const useLastEditStore = create<LastEditStoreState>((set) => ({
  ops: null,
  setLastEdit: (ops) => set({ ops: ops.map((op) => ({ ...op })) }),
}));
