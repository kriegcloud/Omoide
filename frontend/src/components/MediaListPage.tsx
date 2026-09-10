import ListStateView from "./ListState";
import { useListInvalidation } from "../stores/useListStore";
import { useUndoRefresh } from "../context/UndoContext";
import { refreshCachedList } from "../stores/useListStore";
import React, { useEffect, useMemo, useRef, useState } from "react";
import Masonry from "react-masonry-css";
import SortIcon from "@mui/icons-material/Sort";
import RefreshIcon from "@mui/icons-material/Refresh";
import { useInView } from "react-intersection-observer";
import { useSearchParams } from "react-router-dom";
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Container,
  Menu,
  MenuItem,
  Typography,
} from "@mui/material";

import MediaCard from "./MediaCard";
import { useListStore, defaultListState } from "../stores/useListStore";
import { useTaskCompletionVersion } from "../TaskEventsContext";
import { CursorPage, Media } from "../types";
import { useSelection } from "../context/SelectionContext";
import { useGridSelection } from "../hooks/useMarqueeSelection";
import MarqueeSelectionBox from "./MarqueeSelectionBox";

export type MediaSortOrder = "newest" | "latest";

export const MEDIA_SORT_LABELS: Record<MediaSortOrder, string> = {
  newest: "Newest first",
  latest: "Recently added",
};

const breakpointColumnsObj = {
  default: 5,
  1600: 4,
  1200: 3,
  900: 3,
  600: 2,
};

interface MediaListPageProps {
  listKeyPrefix: string;
  fetcher: (
    cursor: string | null,
    sort: MediaSortOrder
  ) => Promise<CursorPage<Media>>;
  emptyTitle: string;
  emptyDescription: string;
}

export default function MediaListPage({
  listKeyPrefix,
  fetcher,
  emptyTitle,
  emptyDescription,
}: MediaListPageProps) {
  const { ref: loaderRef, inView } = useInView({ threshold: 0.5 });
  const [searchParams, setSearchParams] = useSearchParams();
  const sortOrder: MediaSortOrder =
    searchParams.get("sort") === "latest" ? "latest" : "newest";
  const [sortMenuAnchorEl, setSortMenuAnchorEl] = useState<null | HTMLElement>(
    null
  );

  const listKey = useMemo(
    () => `${listKeyPrefix}-${sortOrder}`,
    [listKeyPrefix, sortOrder]
  );
  const { items, hasMore, isLoading, error } = useListStore(
    (state) => state.lists[listKey] || defaultListState
  );
  const fetchInitial = useListStore(state => state.fetchInitial);
const loadMore = useListStore(state => state.loadMore);
const clearList = useListStore(state => state.clearList);
const clearListsByPrefix = useListStore(state => state.clearListsByPrefix);

  useListInvalidation(listKey);
  const refreshKey = useTaskCompletionVersion([
    "scan",
    "process_media",
    "batch_edit_media",
    "run_processor_for_media",
    "clean_missing_files",
  ]);
  const [seenRefreshKey, setSeenRefreshKey] = useState(refreshKey);
  const hasNewItems = refreshKey !== seenRefreshKey;
  const gridRef = useRef<HTMLDivElement>(null);
  const { isSelecting, selectedIds, setSelected, beginSelecting, clear } = useSelection();
  useUndoRefresh(`cache:${listKey}`, () => refreshCachedList(listKey));
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

  // fetchInitial skips lists that already have content, so navigating back
  // restores the cached list (and scroll position) instantly.
  useEffect(() => {
    fetchInitial(listKey, () => fetcher(null, sortOrder));
  }, [listKey, fetchInitial, fetcher, sortOrder]);

  useEffect(() => {
    if (inView && hasMore && !isLoading && !error) {
      loadMore(listKey, (cursor) => fetcher(cursor ?? null, sortOrder));
    }
  }, [inView, hasMore, isLoading, error, loadMore, listKey, fetcher, sortOrder]);

  const refetch = () => {
    clearList(listKey);
    fetchInitial(listKey, () => fetcher(null, sortOrder));
  };

  const handleRefresh = () => {
    setSeenRefreshKey(refreshKey);
    // Clear both sort orders of this page's cache, not just the visible one.
    clearListsByPrefix(`${listKeyPrefix}-`);
    fetchInitial(listKey, () => fetcher(null, sortOrder));
  };

  const handleSortMenuOpen = (event: React.MouseEvent<HTMLElement>) => {
    setSortMenuAnchorEl(event.currentTarget);
  };
  const handleSortMenuClose = () => {
    setSortMenuAnchorEl(null);
  };
  const handleSortChange = (newSortOrder: MediaSortOrder) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (newSortOrder === "newest") next.delete("sort");
      else next.set("sort", newSortOrder);
      return next;
    });
    handleSortMenuClose();
  };

  return (
    <Container
      maxWidth="xl"
      sx={{
        bgcolor: "background.default",
        color: "text.primary",
        minHeight: "100vh",
        py: 2,
      }}
    >
      <Box
        display="flex"
        justifyContent="flex-end"
        alignItems="center"
        gap={1}
        mb={2}
      >
        {hasNewItems && (
          <Chip
            color="primary"
            variant="outlined"
            icon={<RefreshIcon />}
            label="New items — Refresh"
            onClick={handleRefresh}
          />
        )}
        <Button
          onClick={handleSortMenuOpen}
          color="inherit"
          startIcon={<SortIcon />}
          sx={{
            bgcolor: "action.hover",
            borderRadius: 2,
            px: 2,
            color: "text.primary",
          }}
        >
          Sort by: {MEDIA_SORT_LABELS[sortOrder]}
        </Button>
        <Menu
          anchorEl={sortMenuAnchorEl}
          open={Boolean(sortMenuAnchorEl)}
          onClose={handleSortMenuClose}
        >
          <MenuItem
            onClick={() => handleSortChange("newest")}
            selected={sortOrder === "newest"}
          >
            {MEDIA_SORT_LABELS.newest}
          </MenuItem>
          <MenuItem
            onClick={() => handleSortChange("latest")}
            selected={sortOrder === "latest"}
          >
            {MEDIA_SORT_LABELS.latest}
          </MenuItem>
        </Menu>
      </Box>

      <ListStateView loading={isLoading && items.length === 0} error={error} empty={!isLoading && items.length === 0} emptyMessage={emptyTitle} action={<Typography color="text.secondary">{emptyDescription}</Typography>} onRetry={refetch} />

      {items.length > 0 && (
        <Box ref={gridRef} sx={{ position: "relative" }}>
          <Masonry
            breakpointCols={breakpointColumnsObj}
            className="my-masonry-grid"
            columnClassName="my-masonry-grid_column"
          >
            {items.map((media: Media) => (
              <div key={media.id}>
                <MediaCard
                  media={media}
                  mediaListKey={listKey}
                  onSelectionClick={onItemClick}
                />
              </div>
            ))}
          </Masonry>
          <MarqueeSelectionBox container={gridRef.current} rect={marqueeRect} />
        </Box>
      )}

      {items.length > 0 && isLoading && (
        <Box textAlign="center" py={3}>
          <CircularProgress sx={{ color: "accent.main" }} />
        </Box>
      )}
      {hasMore && !error && <Box ref={loaderRef} sx={{ height: "10px" }} />}
    </Container>
  );
}
