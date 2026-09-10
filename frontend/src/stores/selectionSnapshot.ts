export interface SelectionSnapshot {
  isSelecting: boolean;
  selectedIds: ReadonlySet<number>;
  toggle: (id: number) => void;
  beginSelecting: () => void;
}

/** A stable bridge for tiles; list metadata and other ids do not change a tile's snapshot. */
export function createSelectionSnapshotStore(initial: SelectionSnapshot) {
  let snapshot = initial;
  const listeners = new Set<() => void>();
  return {
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
    getItemSnapshot(id: number | null) {
      return Number(snapshot.isSelecting) | (id !== null && snapshot.selectedIds.has(id) ? 2 : 0);
    },
    update(next: SelectionSnapshot) {
      const changed = next.isSelecting !== snapshot.isSelecting || next.selectedIds !== snapshot.selectedIds;
      snapshot = next;
      if (changed) listeners.forEach((listener) => listener());
    },
    toggle(id: number) { snapshot.toggle(id); },
    beginSelecting() { snapshot.beginSelecting(); },
  };
}
