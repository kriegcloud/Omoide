import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

export interface UndoableAction {
  id: string;
  label: string;
  undo: () => Promise<void>;
  ttlMs?: number;
}

interface UndoContextValue {
  push: (action: Omit<UndoableAction, "id">) => void;
  dismiss: () => void;
  current: UndoableAction | null;
  refreshVisible: () => Promise<void>;
  registerRefresh: (key: string, refresh: () => Promise<void>) => () => void;
}

const UndoContext = createContext<UndoContextValue>({
  push: () => {},
  dismiss: () => {},
  current: null,
  refreshVisible: async () => {},
  registerRefresh: () => () => {},
});

export function UndoProvider({ children }: { children: ReactNode }) {
  const [current, setCurrent] = useState<UndoableAction | null>(null);
  const sequence = useRef(0);
  const refreshers = useRef(new Map<string, Map<symbol, () => Promise<void>>>());
  const push = useCallback((action: Omit<UndoableAction, "id">) => {
    setCurrent({ ...action, id: `undo-${++sequence.current}`, ttlMs: action.ttlMs ?? 8000 });
  }, []);
  const dismiss = useCallback(() => setCurrent(null), []);
  const registerRefresh = useCallback((key: string, refresh: () => Promise<void>) => {
    const token = Symbol(key);
    const entries = refreshers.current.get(key) ?? new Map();
    entries.set(token, refresh);
    refreshers.current.set(key, entries);
    return () => {
      entries.delete(token);
      if (!entries.size) refreshers.current.delete(key);
    };
  }, []);
  const refreshVisible = useCallback(async () => {
    // Cards sharing a cached list register the same key: fetch that list once.
    const callbacks = Array.from(refreshers.current.values(), (entries) =>
      Array.from(entries.values()).at(-1)!,
    );
    const results = await Promise.allSettled(callbacks.map((refresh) => refresh()));
    const failure = results.find((result) => result.status === "rejected");
    if (failure?.status === "rejected") throw failure.reason;
  }, []);
  const value = useMemo(() => ({ current, push, dismiss, registerRefresh, refreshVisible }),
    [current, push, dismiss, registerRefresh, refreshVisible]);
  return <UndoContext.Provider value={value}>{children}</UndoContext.Provider>;
}

export const useUndo = () => useContext(UndoContext);

/** Register a mounted list, including lists whose last item was just removed. */
export function useUndoRefresh(key: string | undefined, refresh: () => Promise<void>) {
  const { registerRefresh } = useUndo();
  const latest = useRef(refresh);
  latest.current = refresh;
  useEffect(() => {
    if (key === undefined) return;
    return registerRefresh(key, () => latest.current());
  }, [key, registerRefresh]);
}
