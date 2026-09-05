import React, { useEffect, useMemo, useRef, useState } from "react";
import Masonry from "react-masonry-css";
import SortIcon from "@mui/icons-material/Sort";
import RefreshIcon from "@mui/icons-material/Refresh";
import SearchOffIcon from "@mui/icons-material/SearchOff";
import { useInView } from "react-intersection-observer";
import { useSearchParams } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Container,
  Menu,
  MenuItem,
} from "@mui/material";

import MediaCard from "./MediaCard";
import { MediaSkeleton } from "./MediaSkeleton";
import { EmptyState } from "./EmptyState";
import { useListStore, defaultListState } from "../stores/useListStore";
import { useTaskCompletionVersion } from "../TaskEventsContext";
import { CursorPage, Media } from "../types";
import { useSelection } from "../context/SelectionContext";
import { useMarqueeSelection } from "../hooks/useMarqueeSelection";
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
  const { fetchInitial, loadMore, clearList, clearListsByPrefix } =
    useListStore();

  const refreshKey = useTaskCompletionVersion([
    "scan",
    "process_media",
    "batch_edit_media",
  ]);
  const [seenRefreshKey, setSeenRefreshKey] = useState(refreshKey);
  const hasNewItems = refreshKey !== seenRefreshKey;
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

      {error && (
        <Alert
          severity="error"
          sx={{ mb: 2 }}
          action={
            <Button color="inherit" size="small" onClick={refetch}>
              Retry
            </Button>
          }
        >
          {error}
        </Alert>
      )}

      {/* Loading Skeletons */}
      {items.length === 0 && isLoading && (
        <Box
          sx={{
            display: "grid",
            gap: 2,
            gridTemplateColumns: {
              xs: "repeat(2, 1fr)",
              sm: "repeat(3, 1fr)",
              md: "repeat(4, 1fr)",
              lg: "repeat(5, 1fr)",
            },
          }}
        >
          {[...Array(15)].map((_, i) => (
            <Box key={i} sx={{ aspectRatio: "3/4" }}>
              <MediaSkeleton />
            </Box>
          ))}
        </Box>
      )}

      {/* Empty State */}
      {items.length === 0 && !isLoading && !error && (
        <EmptyState
          icon={<SearchOffIcon />}
          title={emptyTitle}
          description={emptyDescription}
        />
      )}

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
