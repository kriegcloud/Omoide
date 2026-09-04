import { useCallback, useState } from "react";

export function usePeopleSelection() {
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(
    () => new Set<number>(),
  );

  const clear = useCallback(() => {
    setSelectedIds(new Set<number>());
  }, []);

  const toggleMode = useCallback(() => {
    setSelectionMode((previous) => {
      if (previous) {
        setSelectedIds(new Set<number>());
      }
      return !previous;
    });
  }, []);

  const toggle = useCallback((personId: number) => {
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (next.has(personId)) {
        next.delete(personId);
      } else {
        next.add(personId);
      }
      return next;
    });
  }, []);

  const setSelected = useCallback((ids: Iterable<number>) => {
    setSelectedIds(new Set(ids));
  }, []);

  const pruneTo = useCallback((ids: Iterable<number>) => {
    const available = new Set(ids);
    setSelectedIds((previous) => {
      if (previous.size === 0) {
        return previous;
      }

      const next = new Set<number>();
      let changed = false;
      previous.forEach((id) => {
        if (available.has(id)) {
          next.add(id);
        } else {
          changed = true;
        }
      });
      return changed ? next : previous;
    });
  }, []);

  return {
    selectionMode,
    selectedIds,
    toggleMode,
    toggle,
    clear,
    setSelected,
    pruneTo,
  };
}
