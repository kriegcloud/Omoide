import { useCallback, useState } from "react";

export function useEntitySelection<TId extends number | string>() {
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<TId>>(
    () => new Set<TId>(),
  );

  const clear = useCallback(() => {
    setSelectedIds(new Set<TId>());
  }, []);

  const toggleMode = useCallback(() => {
    setSelectionMode((previous) => {
      if (previous) setSelectedIds(new Set<TId>());
      return !previous;
    });
  }, []);

  const toggle = useCallback((id: TId) => {
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const setSelected = useCallback((ids: Iterable<TId>) => {
    setSelectedIds(new Set(ids));
  }, []);

  const pruneTo = useCallback((ids: Iterable<TId>) => {
    const available = new Set(ids);
    setSelectedIds((previous) => {
      const next = new Set(
        Array.from(previous).filter((id) => available.has(id)),
      );
      return next.size === previous.size ? previous : next;
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
