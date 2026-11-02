import { useLayoutEffect, useRef, useState } from "react";
import { useHotkey } from "./useHotkey";

/** Keep focus by identity while virtual rows are removed or cursor pages arrive. */
export function useDuplicateGroupFocus(ids: number[], listKey: string, scrollToIndex: (index: number) => void) {
  const [focusedId, setFocusedId] = useState<number | null>(null);
  const previous = useRef({ ids, listKey });
  const awaitingNext = useRef<Set<number> | null>(null);

  useLayoutEffect(() => {
    const old = previous.current;
    previous.current = { ids, listKey };
    if (old.listKey !== listKey) {
      awaitingNext.current = null;
      setFocusedId(null);
      return;
    }
    if (focusedId !== null && !ids.includes(focusedId)) {
      // Prefer the next surviving row in the old queue, never jump backwards.
      const index = old.ids.indexOf(focusedId);
      const next = old.ids.slice(index + 1).find(id => ids.includes(id)) ?? ids[index];
      awaitingNext.current = next === undefined ? new Set(ids) : null;
      setFocusedId(next ?? null);
    } else if (awaitingNext.current) {
      const next = ids.find(id => !awaitingNext.current?.has(id));
      if (next !== undefined) {
        awaitingNext.current = null;
        setFocusedId(next);
      }
    }
  }, [ids, listKey, focusedId]);

  useLayoutEffect(() => {
    const index = focusedId === null ? -1 : ids.indexOf(focusedId);
    if (index >= 0) scrollToIndex(index);
  }, [focusedId, ids, scrollToIndex]);

  const move = (direction: number) => {
    awaitingNext.current = null;
    const index = focusedId === null ? -1 : ids.indexOf(focusedId);
    const next = index < 0 ? (direction > 0 ? 0 : ids.length - 1) : Math.max(0, Math.min(ids.length - 1, index + direction));
    setFocusedId(ids[next] ?? null);
  };
  useHotkey({ key: "j", description: "Focus next duplicate group" }, () => move(1), { scope: "page" });
  useHotkey({ key: "k", description: "Focus previous duplicate group" }, () => move(-1), { scope: "page" });
  useHotkey({ key: "Escape", description: "Clear duplicate group focus" }, () => {
    awaitingNext.current = null;
    setFocusedId(null);
  }, { scope: "page", enabled: focusedId !== null });

  return { focusedId, setFocusedId };
}
