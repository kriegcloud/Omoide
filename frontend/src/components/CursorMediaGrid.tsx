import React, { useCallback, useEffect, useRef, useState } from "react";
import Masonry from "react-masonry-css";
import { Alert, Box, CircularProgress } from "@mui/material";
import { useInView } from "react-intersection-observer";
import MediaCard from "./MediaCard";
import { CursorPage, Media } from "../types";
import { useSelection } from "../context/SelectionContext";
import { useMarqueeSelection } from "../hooks/useMarqueeSelection";
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
  const inFlightRef = useRef(false);
  const gridRef = useRef<HTMLDivElement>(null);
  const { isSelecting, selectedIds, setSelected } = useSelection();
  const { marqueeRect, onItemClick } = useMarqueeSelection<number>({
    containerRef: gridRef,
    itemSelector: "[data-selectable-id]",
    getId: (element) => Number(element.dataset.selectableId),
    enabled: isSelecting,
    selectedIds,
    onSelectionChange: setSelected,
  });

  const loadPage = useCallback(
    async (fromCursor: string | null, replace: boolean) => {
      if (inFlightRef.current) return;
      const seq = ++requestSeq.current;
      inFlightRef.current = true;
      setIsLoading(true);
      setError(null);
      try {
        const page = await fetcher(fromCursor);
        if (seq !== requestSeq.current) return;
        setItems((prev) => {
          const next = replace ? page.items : [...prev, ...page.items];
          onItemsChange?.(next);
          return next;
        });
        setCursor(page.next_cursor);
        setHasMore(page.next_cursor !== null);
      } catch (err) {
        if (seq !== requestSeq.current) return;
        setError(err instanceof Error ? err.message : "Failed to load media");
        setHasMore(false);
      } finally {
        if (seq === requestSeq.current) {
          inFlightRef.current = false;
          setIsLoading(false);
        }
      }
    },
    [fetcher, onItemsChange]
  );

  useEffect(() => {
    // Invalidate any previous list request. A new list must be allowed to
    // start immediately even if the previous list is still resolving.
    requestSeq.current += 1;
    inFlightRef.current = false;
    setItems([]);
    setCursor(null);
    setHasMore(true);
    void loadPage(null, true);
  }, [listKey, refreshToken, loadPage]);

  useEffect(() => {
    if (inView && hasMore && !isLoading && !inFlightRef.current && !error) {
      void loadPage(cursor, false);
    }
  }, [inView, hasMore, isLoading, error, cursor, loadPage]);

  return (
    <Box ref={gridRef} sx={{ position: "relative" }}>
      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}
      {items.length === 0 && !isLoading && !error && empty}
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
