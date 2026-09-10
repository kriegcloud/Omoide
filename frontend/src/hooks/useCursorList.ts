import { mutationBus } from "../stores/mutationBus";
import { ListRevision, mergeUnique } from "../stores/listReconciliation";
import { useUndoRefresh } from "../context/UndoContext";
import { useCallback, useEffect, useRef, useState, useId } from "react";
import { useInView } from "react-intersection-observer";

export interface CursorListPage<T> {
  items: T[];
  next_cursor: string | null;
  total: number;
}

export interface CursorListResult<T> {
  items: T[];
  total: number;
  hasMore: boolean;
  isLoading: boolean;
  error: string | null;
  loaderRef: (node?: Element | null) => void;
  selectedIds: Set<number>;
  setSelectedIds: (next: Set<number>) => void;
  toggleSelected: (id: number) => void;
  selectVisible: () => void;
  clearSelection: () => void;
  removeItems: (ids: Iterable<number>, removedCount?: number) => void;
  refetch: () => Promise<void>;
}

export function useCursorList<T extends { id: number }>(
  fetcher: (cursor: string | null) => Promise<CursorListPage<T>>,
  refreshKey = 0
): CursorListResult<T> {
  const [items, setItems] = useState<T[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  const generationRef = useRef(0);
  const removalSequence = useRef(0);
  const countedRemovals = useRef(new Map<number, number>());
  const additionalRemovals = useRef(0);
  const revision = useRef(new ListRevision());
  const nextCursorRef = useRef<string | null>(null);
  const inFlightRef = useRef(false);

  const { ref: loaderRef, inView } = useInView({ threshold: 0.5 });

  const fetchPage = useCallback(
    async (cursor: string | null, append: boolean, throwOnError = false) => {
      if (!append) {
        generationRef.current += 1;
        nextCursorRef.current = null;
        setHasMore(false);
      }
      const generation = generationRef.current;
      const started = revision.current.revision;
      const removedAtStart = removalSequence.current;
      const additionalAtStart = additionalRemovals.current;
      inFlightRef.current = true;
      setIsLoading(true);
      setError(null);
      try {
        const page = await fetcher(cursor);
        if (generation !== generationRef.current) return;
        const incoming = revision.current.reconcile(page.items, started, !append);
        setItems((prev) => (append ? mergeUnique(prev, incoming) : incoming));
        const removedDuringRequest = new Set([...countedRemovals.current].filter(([, sequence]) => sequence > removedAtStart).map(([id]) => id));
        const incomingIds = new Set(incoming.map(item => item.id));
        for (const item of page.items) if (!incomingIds.has(item.id)) removedDuringRequest.add(item.id);
        setTotal(Math.max(0, page.total - removedDuringRequest.size - (additionalRemovals.current - additionalAtStart)));
        if (!append) for (const [id, sequence] of countedRemovals.current) if (sequence <= removedAtStart) countedRemovals.current.delete(id);
        nextCursorRef.current = page.next_cursor;
        setHasMore(Boolean(page.next_cursor));
        if (!append) setSelectedIds(new Set());
      } catch (e) {
        if (generation !== generationRef.current) return;
        setError(e instanceof Error ? e.message : "Failed to load media");
        if (throwOnError) throw e;
      } finally {
        if (generation === generationRef.current) {
          inFlightRef.current = false;
          setIsLoading(false);
        }
      }
    },
    [fetcher]
  );

  useEffect(() => {
    fetchPage(null, false);
    return () => { generationRef.current += 1; };
  }, [fetchPage, refreshKey]);

  useEffect(() => {
    if (
      inView &&
      hasMore &&
      !isLoading &&
      !inFlightRef.current &&
      !error &&
      nextCursorRef.current
    ) {
      fetchPage(nextCursorRef.current, true);
    }
  }, [inView, hasMore, isLoading, error, fetchPage]);

  const toggleSelected = useCallback((id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }, []);

  const selectVisible = useCallback(() => {
    setSelectedIds(new Set(items.map((item) => item.id)));
  }, [items]);

  const clearSelection = useCallback(() => setSelectedIds(new Set()), []);

  const removeItems = useCallback(
    (ids: Iterable<number>, removedCount?: number) => {
      const idSet = ids instanceof Set ? ids : new Set(ids);
      const sequence = ++removalSequence.current;
      for (const id of idSet) countedRemovals.current.set(id, sequence);
      additionalRemovals.current += Math.max(0, (removedCount ?? idSet.size) - idSet.size);
      revision.current.remove(idSet);
      setItems((prev) => prev.filter((item) => !idSet.has(item.id)));
      setTotal((prev) => Math.max(0, prev - (removedCount ?? idSet.size)));
      setSelectedIds((prev) => {
        const next = new Set(prev);
        idSet.forEach((id) => next.delete(id));
        return next;
      });
    },
    []
  );

  const refetch = useCallback(() => fetchPage(null, false), [fetchPage]);

  const itemsRef = useRef(items);
  itemsRef.current = items;
  useEffect(() => mutationBus.subscribe(event => {
    if (event.type === "media:deleted") {
      const visible = event.ids.filter(id => itemsRef.current.some(item => item.id === id));
      revision.current.remove(event.ids);
      removeItems(visible);
    } else if (event.type === "media:updated") {
      revision.current.patch(event.items);
      const patches = new Map(event.items.map(item => [item.id, item]));
      setItems(previous => previous.map(item => patches.has(item.id) ? { ...item, ...patches.get(item.id) } : item));
    } else if (event.type === "media:moved" || (event.type === "list:invalidate" && event.prefix === "")) {
      void refetch();
    }
  }), [removeItems, refetch]);

  const refreshId = useId();
  useUndoRefresh(`cursor-list:${refreshId}`, () => fetchPage(null, false, true));

  return {
    items,
    total,
    hasMore,
    isLoading,
    error,
    loaderRef,
    selectedIds,
    setSelectedIds,
    toggleSelected,
    selectVisible,
    clearSelection,
    removeItems,
    refetch,
  };
}
