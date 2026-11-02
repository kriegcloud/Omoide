import { acknowledgeQuery, operationVersion, optimisticOperations, projectList, registerQueryEntities, registerQueryRefresh } from "../stores/optimisticOperations";
import { mutationBus } from "../stores/mutationBus";
import { ListRevision, mergeUnique } from "../stores/listReconciliation";
import ListState from "./ListState";
import { useUndoRefresh } from "../context/UndoContext";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Gallery from "./Gallery";
import { Box, CircularProgress } from "@mui/material";
import { useInView } from "react-intersection-observer";
import MediaCard from "./MediaCard";
import { CursorPage, Media } from "../types";
import { useSelection } from "../context/SelectionContext";
import { useGridSelection } from "../hooks/useMarqueeSelection";
import MarqueeSelectionBox from "./MarqueeSelectionBox";


interface Props {
  listKey: string;
  fetcher: (cursor: string | null, signal: AbortSignal) => Promise<CursorPage<Media>>;
  empty?: React.ReactNode;
  /** Bumping this value revalidates while retaining the current list. */
  refreshToken?: unknown;
  optimistic?: boolean;
  /** Lane K can provide a windowed renderer without taking ownership of this adapter. */
  renderItems?: (items: Media[], onItemClick: ReturnType<typeof useGridSelection<number>>["onItemClick"]) => React.ReactNode;
  onItemsChange?: (items: Media[]) => void;
}

/** Self-contained cursor-paginated masonry grid (local state, no list store). */
export function CursorMediaGrid({
  listKey,
  fetcher,
  empty,
  refreshToken,
  onItemsChange,
  optimistic = false,
  renderItems,
}: Props) {
  const { ref: loaderRef, inView } = useInView({ threshold: 0.5 });
  const [operationsRevision, notifyOperations] = useState(0);
  useEffect(() => optimistic ? optimisticOperations.subscribe(() => notifyOperations(value => value + 1)) : undefined, [optimistic]);
  const [items, setItems] = useState<Media[]>([]);
  const visibleItems = useMemo(() => {
    void operationsRevision;
    return optimistic ? projectList(listKey, "media", items).items : items;
  }, [optimistic, listKey, items, operationsRevision]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestSeq = useRef(0);
  const controller = useRef(new AbortController());
  const revision = useRef(new ListRevision());
  const inFlightRef = useRef(false);
  const gridRef = useRef<HTMLDivElement>(null);
  const { isSelecting, selectedIds, setSelected, beginSelecting, clear } = useSelection();
  const { marqueeRect, onItemClick } = useGridSelection<number>({
    listKey,
    loadedCount: visibleItems.length,
    hasMore,
    containerRef: gridRef,
    itemSelector: "[data-selectable-id]",
    getId: (element) => Number(element.dataset.selectableId),
    selecting: isSelecting,
    allowPlainDragOnItems: false,
    onEnterSelection: beginSelecting,
    onExitSelection: clear,
    selectedIds,
    onSelectionChange: setSelected,
  });

  const loadPage = useCallback(
    async (fromCursor: string | null, replace: boolean, throwOnError = false) => {
      if (inFlightRef.current) return;
      const seq = ++requestSeq.current;
      const started = revision.current.revision;
      const operationsStarted = optimistic ? operationVersion() : 0;
      inFlightRef.current = true;
      setIsLoading(true);
      setError(null);
      try {
        const page = await fetcher(fromCursor, controller.current.signal);
        if (seq !== requestSeq.current) return;
        if (replace && optimistic) acknowledgeQuery(listKey, operationsStarted);
        const incoming = revision.current.reconcile(page.items, started, replace);
        setItems((prev) => replace ? incoming : mergeUnique(prev, incoming));
        setCursor(page.next_cursor);
        setHasMore(page.next_cursor !== null);
      } catch (err) {
        if (seq !== requestSeq.current) return;
        setError(err instanceof Error ? err.message : "Failed to load media");
        setHasMore(false);
        if (throwOnError) throw err;
      } finally {
        if (seq === requestSeq.current) {
          inFlightRef.current = false;
          setIsLoading(false);
        }
      }
    },
    [fetcher, optimistic, listKey]
  );

  useUndoRefresh(`cursor:${listKey}`, async () => {
    requestSeq.current += 1;
    inFlightRef.current = false;
    await loadPage(null, true, true);
  });

  useEffect(() => optimistic ? registerQueryRefresh(listKey, async () => {
    requestSeq.current += 1;
    inFlightRef.current = false;
    await loadPage(null, true, true);
  }) : undefined, [optimistic, listKey, loadPage]);

  const previousKey = useRef(listKey);
  useEffect(() => {
    controller.current.abort();
    controller.current = new AbortController();
    // Invalidate any previous list request. A new list must be allowed to
    // start immediately even if the previous list is still resolving.
    requestSeq.current += 1;
    inFlightRef.current = false;
    if (previousKey.current !== listKey) {
      setItems([]);
      revision.current = new ListRevision();
      previousKey.current = listKey;
    }
    setCursor(null);
    setHasMore(true);
    void loadPage(null, true);
    return () => { controller.current.abort(); requestSeq.current += 1; inFlightRef.current = false; };
  }, [listKey, refreshToken, loadPage]);

  useEffect(() => {
    if (inView && hasMore && !isLoading && !inFlightRef.current && !error) {
      void loadPage(cursor, false);
    }
  }, [inView, hasMore, isLoading, error, cursor, loadPage]);

  useEffect(() => { onItemsChange?.(visibleItems); }, [visibleItems, onItemsChange]);
  useEffect(() => {
    let queued = false;
    let active = true;
    const unsubscribe = mutationBus.subscribe(event => {
    if (event.type === "media:deleted") {
      revision.current.remove(event.ids);
      const ids = new Set(event.ids);
      setItems(previous => previous.filter(item => !ids.has(item.id)));
    } else if (event.type === "media:updated") {
      revision.current.patch(event.items);
      const patches = new Map(event.items.map(item => [item.id, item]));
      setItems(previous => previous.map(item => patches.has(item.id) ? { ...item, ...patches.get(item.id) } : item));
    }
    if (event.type !== "list:invalidate" || listKey.startsWith(event.prefix)) {
      if (queued) return;
      queued = true;
      queueMicrotask(() => {
        queued = false;
        if (!active) return;
        requestSeq.current += 1;
        inFlightRef.current = false;
        void loadPage(null, true);
      });
    }
    });
    return () => { active = false; unsubscribe(); };
  }, [listKey, loadPage]);

  useEffect(() => optimistic ? registerQueryEntities(listKey, items.map(item => `media:${item.id}` as const)) : undefined, [optimistic, listKey, items]);

  return (
    <Box ref={gridRef} sx={{ position: "relative" }}>
      <ListState loading={isLoading && visibleItems.length === 0} error={error} empty={!isLoading && visibleItems.length === 0} action={empty} onRetry={() => { void loadPage(cursor, visibleItems.length === 0); }} />
      {visibleItems.length > 0 && (renderItems ? renderItems(visibleItems, onItemClick) : (
        <Gallery>
          {visibleItems.map((media) => (
            <div key={media.id}>
              <MediaCard
                media={media}
                mediaListKey={listKey}
                onSelectionClick={onItemClick}
              />
            </div>
          ))}
        </Gallery>
      ))}
      {isLoading && (
        <Box textAlign="center" py={3}>
          <CircularProgress />
        </Box>
      )}
      {hasMore && !error && <Box ref={loaderRef} sx={{ height: 10 }} />}
      <MarqueeSelectionBox container={gridRef.current} rect={marqueeRect} />
    </Box>
  );
}
