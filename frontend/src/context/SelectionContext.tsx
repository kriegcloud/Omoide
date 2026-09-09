import React, {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import { useLocation } from "react-router-dom";

interface SelectionContextValue {
  listKey: string | null;
  loadedCount: number;
  hasMore: boolean;
  setListKey: (key: string | null) => void;
  setListInfo: (loadedCount: number, hasMore: boolean) => void;
  syncRoute: (pathname: string) => void;
  isSelecting: boolean;
  selectedIds: Set<number>;
  beginSelecting: () => void;
  toggleSelecting: () => void;
  toggle: (id: number) => void;
  setSelected: (ids: Iterable<number>) => void;
  clear: () => void;
}

const defaultValue: SelectionContextValue = {
  listKey: null,
  loadedCount: 0,
  hasMore: false,
  setListKey: () => {},
  setListInfo: () => {},
  syncRoute: () => {},
  isSelecting: false,
  selectedIds: new Set(),
  beginSelecting: () => {},
  toggleSelecting: () => {},
  toggle: () => {},
  setSelected: () => {},
  clear: () => {},
};

export const SelectionContext = createContext<SelectionContextValue>(defaultValue);

export const SelectionProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const [isSelecting, setIsSelecting] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  const [listKey, updateListKey] = useState<string | null>(null);
  const [listInfo, updateListInfo] = useState({ loadedCount: 0, hasMore: false });
  const listKeyRef = useRef<string | null>(null);
  const selectionKeyRef = useRef<string | null>(null);
  const registrationRef = useRef(0);
  const routeRef = useRef<{ pathname: string; registration: number } | null>(null);

  const setListKey = useCallback((key: string | null) => {
    registrationRef.current += 1;
    listKeyRef.current = key;
    updateListKey(key);
    if (key === null) updateListInfo({ loadedCount: 0, hasMore: false });
    // Changing filters on the same route must not carry ids into another list.
    if (key !== null && selectionKeyRef.current !== null && key !== selectionKeyRef.current) {
      setSelectedIds(new Set());
      setIsSelecting(false);
      selectionKeyRef.current = null;
    }
  }, []);
  const setListInfo = useCallback((loadedCount: number, hasMore: boolean) => {
    updateListInfo((previous) => previous.loadedCount === loadedCount && previous.hasMore === hasMore
      ? previous : { loadedCount, hasMore });
  }, []);

  const beginSelecting = useCallback(() => {
    selectionKeyRef.current = listKeyRef.current;
    setIsSelecting(true);
  }, []);

  const toggleSelecting = useCallback(() => {
    selectionKeyRef.current = listKeyRef.current;
    setIsSelecting((prev) => {
      if (prev) setSelectedIds(new Set());
      return !prev;
    });
  }, []);

  const toggle = useCallback((id: number) => {
    selectionKeyRef.current = listKeyRef.current;
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const clear = useCallback(() => {
    selectionKeyRef.current = null;
    setSelectedIds(new Set());
    setIsSelecting(false);
  }, []);

  const setSelected = useCallback((ids: Iterable<number>) => {
    selectionKeyRef.current = listKeyRef.current;
    setSelectedIds(new Set(ids));
  }, []);

  const syncRoute = useCallback((pathname: string) => {
    const previous = routeRef.current;
    if (previous?.pathname === pathname) return;
    const next = { pathname, registration: registrationRef.current };
    routeRef.current = next;
    queueMicrotask(() => {
      if (routeRef.current !== next) return;
      const sameListRegistered = listKeyRef.current !== null &&
        listKeyRef.current === selectionKeyRef.current &&
        registrationRef.current > (previous?.registration ?? -1);
      if (previous && !sameListRegistered) clear();
      next.registration = registrationRef.current;
    });
  }, [clear]);

  const value = useMemo(
    () => ({ listKey, ...listInfo, setListKey, setListInfo, syncRoute, isSelecting, selectedIds, beginSelecting, toggleSelecting, toggle, setSelected, clear }),
    [listKey, listInfo, setListKey, setListInfo, syncRoute, isSelecting, selectedIds, beginSelecting, toggleSelecting, toggle, setSelected, clear]
  );

  return (
    <SelectionContext.Provider value={value}>
      {children}
    </SelectionContext.Provider>
  );
};

export const useSelection = () => useContext(SelectionContext);

/** Lives below the router; the selection provider deliberately lives above it. */
export function SelectionRouteSync() {
  const { pathname } = useLocation();
  const { syncRoute } = useSelection();
  useLayoutEffect(() => syncRoute(pathname), [pathname, syncRoute]);
  return null;
}

export function useSelectionList(key: string | null, loadedCount: number, hasMore: boolean) {
  const { pathname } = useLocation();
  const { setListKey, setListInfo } = useSelection();
  useLayoutEffect(() => {
    if (key === null) return;
    setListKey(key);
    return () => setListKey(null);
  }, [key, pathname, setListKey]);
  useLayoutEffect(() => {
    if (key !== null) setListInfo(loadedCount, hasMore);
  }, [key, pathname, loadedCount, hasMore, setListInfo]);
}
