import { mutationBus } from "../stores/mutationBus";
import { ListRevision, mergeUnique } from "../stores/listReconciliation";
import ListState from "./ListState";
import { useUndoRefresh } from "../context/UndoContext";
import React, { useCallback, useEffect, useRef, useState } from "react";
import Masonry from "react-masonry-css";
import { Box, CircularProgress } from "@mui/material";
import { useInView } from "react-intersection-observer";
import MediaCard from "./MediaCard";
import { CursorPage, Media } from "../types";
import { useSelection } from "../context/SelectionContext";
import { useGridSelection } from "../hooks/useMarqueeSelection";
import MarqueeSelectionBox from "./MarqueeSelectionBox";

const breakpointColumnsObj = {
  default: 5,
  1600: 4,
  1200: 3,
  900: 3,
  600: 2,
};

interface Props {
  listKey: string;
  fetcher: (cursor: string | null) => Promise<CursorPage<Media>>;
  empty?: React.ReactNode;
  /** Bumping this value clears and refetches the list. */
  refreshToken?: unknown;
  onItemsChange?: (items: Media[]) => void;
}

/** Self-contained cursor-paginated masonry grid (local state, no list store). */
export function CursorMediaGrid({
  listKey,
  fetcher,
  empty,
  refreshToken,
  onItemsChange,
}: Props) {
  const { ref: loaderRef, inView } = useInView({ threshold: 0.5 });
  const [items, setItems] = useState<Media[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestSeq = useRef(0);
  const revision = useRef(new ListRevision());
  const inFlightRef = useRef(false);
  const gridRef = useRef<HTMLDivElement>(null);
  const { isSelecting, selectedIds, setSelected, beginSelecting, clear } = useSelection();
  const { marqueeRect, onItemClick } = useGridSelection<number>({
    listKey,
    loadedCount: items.length,
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
      inFlightRef.current = true;
      setIsLoading(true);
      setError(null);
      try {
        const page = await fetcher(fromCursor);
        if (seq !== requestSeq.current) return;
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
    [fetcher]
  );

  useUndoRefresh(`cursor:${listKey}`, async () => {
    requestSeq.current += 1;
    inFlightRef.current = false;
    await loadPage(null, true, true);
  });

  useEffect(() => {
    // Invalidate any previous list request. A new list must be allowed to
    // start immediately even if the previous list is still resolving.
    requestSeq.current += 1;
    inFlightRef.current = false;
    setItems([]);
    setCursor(null);
    setHasMore(true);
    void loadPage(null, true);
    return () => { requestSeq.current += 1; inFlightRef.current = false; };
  }, [listKey, refreshToken, loadPage]);

  useEffect(() => {
    if (inView && hasMore && !isLoading && !inFlightRef.current && !error) {
      void loadPage(cursor, false);
    }
  }, [inView, hasMore, isLoading, error, cursor, loadPage]);

  useEffect(() => { onItemsChange?.(items); }, [items, onItemsChange]);
  useEffect(() => mutationBus.subscribe(event => {
    if (event.type === "media:deleted") {
      revision.current.remove(event.ids);
      const ids = new Set(event.ids);
      setItems(previous => previous.filter(item => !ids.has(item.id)));
    } else if (event.type === "media:updated") {
      revision.current.patch(event.items);
      const patches = new Map(event.items.map(item => [item.id, item]));
      setItems(previous => previous.map(item => patches.has(item.id) ? { ...item, ...patches.get(item.id) } : item));
    } else if (event.type === "media:moved" || (event.type === "list:invalidate" && listKey.startsWith(event.prefix))) {
      requestSeq.current += 1;
      inFlightRef.current = false;
      void loadPage(null, true);
    }
  }), [listKey, loadPage]);

  return (
    <Box ref={gridRef} sx={{ position: "relative" }}>
      <ListState loading={isLoading && items.length === 0} error={error} empty={!isLoading && items.length === 0} action={empty} onRetry={() => { void loadPage(cursor, items.length === 0); }} />
      {items.length > 0 && (
        <Masonry
          breakpointCols={breakpointColumnsObj}
          className="my-masonry-grid"
          columnClassName="my-masonry-grid_column"
        >
          {items.map((media) => (
            <div key={media.id}>
              <MediaCard
                media={media}
                mediaListKey={listKey}
                onSelectionClick={onItemClick}
              />
            </div>
          ))}
        </Masonry>
      )}
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
