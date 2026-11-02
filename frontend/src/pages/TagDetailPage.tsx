import Gallery from "../components/Gallery";
import GalleryPage from "../components/GalleryPage";
import { useListInvalidation } from "../stores/useListStore";
import { useMutationRefresh } from "../context/UndoContext";
import { readCachedList } from "../stores/useListStore";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { useParams, Link as RouterLink } from "react-router-dom";
import { useInView } from "react-intersection-observer";
import { infiniteScrollObserver } from "../hooks/infiniteScroll";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Typography,
  ToggleButton,
  ToggleButtonGroup,
} from "@mui/material";
import MediaCard from "../components/MediaCard";
import PersonCard from "../components/PersonCard";
import { Tag, Media, Person } from "../types";
import { getTag } from "../services/tag";
import { getMediaList } from "../services/media";
import { useListStore, defaultListState } from "../stores/useListStore";
import { useSelection } from "../context/SelectionContext";
import { useGridSelection } from "../hooks/useMarqueeSelection";
import MarqueeSelectionBox from "../components/MarqueeSelectionBox";

const TEXT_PRIMARY = "text.primary";

type MediaFilter = "all" | "image" | "video";

export default function TagDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [loadedTag, setTag] = useState<Tag | null>(null);
  const tag = loadedTag?.id === Number(id) ? loadedTag : null;
  const [tagError, setTagError] = useState<string | null>(null);
  const [retryTick, setRetryTick] = useState(0);
  const [mediaFilter, setMediaFilter] = useState<MediaFilter>("all");
  const { ref: loaderRef, inView } = useInView(infiniteScrollObserver);
  const mediaGridRef = useRef<HTMLDivElement>(null);
  const { isSelecting, selectedIds, setSelected, beginSelecting, clear } = useSelection();

  useMutationRefresh(["tag:changed", "media:deleted", "media:updated", "media:moved", "list:invalidate"], () => setRetryTick(value => value + 1));

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setTag(current => current?.id === Number(id) ? current : null);
    setTagError(null);
    getTag(id)
      .then((tagData) => {
        if (!cancelled) setTag(tagData);
      })
      .catch((error) => {
        if (!cancelled) {
          setTagError(
            error instanceof Error ? error.message : "Failed to load tag"
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [id, retryTick]);

  const tagName = tag?.name ?? null;
  const listKey = tagName ? `tag-media-${tagName}` : "";
  const listState = useListStore((state) =>
    listKey ? readCachedList<Media>(state, listKey) : undefined
  );
  const mediaItems = listState?.items ?? defaultListState.items;
  const hasMore = listState?.hasMore ?? defaultListState.hasMore;
  const isLoading = listState?.isLoading ?? defaultListState.isLoading;
  const listError = listState?.error ?? defaultListState.error;
  const fetchInitial = useListStore((state) => state.fetchInitial);
  const loadMore = useListStore((state) => state.loadMore);
  useListInvalidation(listKey);
  const clearList = useListStore((state) => state.clearList);

  const { marqueeRect, onItemClick } = useGridSelection<number>({
    listKey: `${listKey}:${mediaFilter}`,
    loadedCount: mediaItems.filter((item) => mediaFilter === "all" || (mediaFilter === "video" ? typeof item.duration === "number" : typeof item.duration !== "number")).length,
    hasMore,
    containerRef: mediaGridRef,
    itemSelector: "[data-selectable-id]",
    getId: (element) => Number(element.dataset.selectableId),
    selecting: isSelecting,
    allowPlainDragOnItems: false,
    onEnterSelection: beginSelecting,
    onExitSelection: clear,
    selectedIds,
    onSelectionChange: setSelected,
  });

  useEffect(() => {
    if (!tagName || !listKey) return;
    fetchInitial(listKey, () => getMediaList(null, "newest", [tagName]));
  }, [tagName, listKey, fetchInitial]);

  useEffect(() => {
    if (!tagName || !listKey) return;
    if (inView && hasMore && !isLoading && !listError) {
      loadMore(listKey, (cursor) =>
        getMediaList(cursor ?? null, "newest", [tagName])
      );
    }
  }, [inView, hasMore, isLoading, listError, listKey, loadMore, tagName]);

  const filteredMediaItems = useMemo(() => {
    const items = mediaItems as Media[];
    if (mediaFilter === "all") return items;
    if (mediaFilter === "video") {
      return items.filter((item) => typeof item.duration === "number");
    }
    return items.filter((item) => typeof item.duration !== "number");
  }, [mediaFilter, mediaItems]);
  const mediaIds = useMemo(
    () => filteredMediaItems.map((m) => m.id),
    [filteredMediaItems]
  );

  if (tagError && !tag) {
    return (
      <Box
        p={4}
        display="flex"
        flexDirection="column"
        alignItems="center"
        gap={2}
      >
        <Alert severity="error">{tagError}</Alert>
        <Button variant="contained" onClick={() => setRetryTick((t) => t + 1)}>
          Retry
        </Button>
      </Box>
    );
  }

  if (!tag) {
    return (
      <Box p={4} textAlign="center">
        <CircularProgress />
      </Box>
    );
  }

  return (
    <GalleryPage title={`Tag: #${tag.name}`} count={filteredMediaItems.length}
      filters={        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            flexWrap: "wrap",
            gap: 2,
            mb: 0,
          }}
        >

          <ToggleButtonGroup
            exclusive
            size="small"
            value={mediaFilter}
            onChange={(_, next) => {
              if (next) setMediaFilter(next);
            }}
          >
            <ToggleButton value="all">All</ToggleButton>
            <ToggleButton value="image">Images</ToggleButton>
            <ToggleButton value="video">Videos</ToggleButton>
          </ToggleButtonGroup>
        </Box>}>

      {tagError && <Alert severity="error" sx={{ mb: 2 }}>{tagError}</Alert>}

      {/* Media Section */}
      <Box mb={6}>

        {listError && (
          <Alert
            severity="error"
            sx={{ mb: 2 }}
            action={
              <Button
                color="inherit"
                size="small"
                onClick={() => {
                  clearList(listKey);
                  fetchInitial(listKey, () =>
                    getMediaList(null, "newest", [tag.name])
                  );
                }}
              >
                Retry
              </Button>
            }
          >
            {listError}
          </Alert>
        )}
        <Box ref={mediaGridRef} sx={{ position: "relative" }}><Gallery>
          {filteredMediaItems.map((m: Media) => (
            <div key={m.id}>
              <MediaCard
                media={m}
                mediaListKey={listKey}
                navigationContext={{ ids: mediaIds }}
                onSelectionClick={onItemClick}
              />
            </div>
          ))}
        </Gallery>
          <MarqueeSelectionBox
            container={mediaGridRef.current}
            rect={marqueeRect}
          />
        </Box>
        {isLoading && (
          <Box textAlign="center" py={3}>
            <CircularProgress />
          </Box>
        )}
        {!isLoading && !listError && filteredMediaItems.length === 0 && (
          <Typography color="text.secondary" sx={{ py: 2 }}>
            No media with this tag.
          </Typography>
        )}
        {hasMore && !listError && (
          <Box ref={loaderRef} sx={{ height: "10px" }} />
        )}
      </Box>

      {/* People Section */}
      <Box>
        <Typography variant="h5" gutterBottom sx={{ color: TEXT_PRIMARY }}>
          People
        </Typography>
        <Gallery>
          {(tag.persons ?? []).map((p: Person) => (
            <div key={p.id}>
              <Box
                component={RouterLink}
                to={`/person/${p.id}`}
                sx={{ textDecoration: "none" }}
              >
                <PersonCard person={p} />
              </Box>
            </div>
          ))}
        </Gallery>
      </Box>
    </GalleryPage>
  );
}
