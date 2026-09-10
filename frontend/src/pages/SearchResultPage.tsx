import { mutationBus } from "../stores/mutationBus";
import { ListRevision, mergeUnique } from "../stores/listReconciliation";
import { useUndoRefresh } from "../context/UndoContext";
import { refreshCachedList, useListInvalidation } from "../stores/useListStore";
import { getMedia } from "../services/media";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Container,
  Typography,
  ToggleButton,
  ToggleButtonGroup,
} from "@mui/material";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useInView } from "react-intersection-observer";
import Masonry from "react-masonry-css";
import { useLocation, useSearchParams } from "react-router-dom";
import MediaCard from "../components/MediaCard";
import PersonCard from "../components/PersonCard";
import TagCard from "../components/TagCard";
import { defaultListState, useListStore } from "../stores/useListStore";
import {
  MediaPreview,
  Person,
  PersonReadSimple,
  SceneSearchResult,
  Tag,
} from "../types";
import { searchCombined, searchScenes, searchTags } from "../services/search";
import SceneResultCard from "../components/SceneResultCard";
import { API } from "../config";
import { useSelection } from "../context/SelectionContext";
import { useGridSelection } from "../hooks/useMarqueeSelection";
import MarqueeSelectionBox from "../components/MarqueeSelectionBox";

const ITEMS_PER_PAGE = 30;

const breakpointColumnsObj = {
  default: 5,
  1600: 4,
  1200: 3,
  900: 2,
  600: 2,
};

type MediaFilter = "all" | "image" | "video";

function isMedia(item: MediaPreview | Person | Tag): item is MediaPreview {
  return item && "thumbnail_path" in item;
}
function isTag(item: MediaPreview | Person | Tag): item is Tag {
  return (
    item && !("tags" in item) && "name" in item && !("profile_face" in item)
  );
}
function isVideoMedia(item: MediaPreview): boolean {
  return typeof item.duration === "number";
}

// ---------------------------------------------------------------------------
// Local state shape for the media/combined category
// ---------------------------------------------------------------------------
interface MediaSearchState {
  persons: PersonReadSimple[];
  media: MediaPreview[];
  nextCursor: string | null;
  isLoading: boolean;
  hasMore: boolean;
  error: string | null;
}

const emptyMediaState: MediaSearchState = {
  persons: [],
  media: [],
  nextCursor: null,
  isLoading: false,
  hasMore: false,
  error: null,
};

export default function SearchResultsPage() {
  const [searchParams] = useSearchParams();
  const rawCategory = searchParams.get("category");
  const category: "media" | "tag" | "scene" =
    rawCategory === "tag" || rawCategory === "scene" ? rawCategory : "media";
  const query = searchParams.get("query") || "";
  const location = useLocation();

  // Declare before any effects so closures always see the initialized value
  const preloadedState = location.state as {
    items: (MediaPreview | Person | Tag | SceneSearchResult)[];
    searchType: "image";
  } | null;
  const isImageSearch = preloadedState?.searchType === "image";

  // ---- list-store state (tag + scene categories) -------------------------
  const listKey = useMemo(() => {
    if (category === "media" || !query) return "";
    const params = new URLSearchParams({ query });
    return `${API}/api/search/${category}?${params.toString()}`;
  }, [category, query]);

  useListInvalidation(listKey);
  const listState = useListStore((state) => state.lists[listKey]);
  const listItems = listState?.items || [];
  const listHasMore = listState?.hasMore ?? defaultListState.hasMore;
  const listIsLoading = listState?.isLoading ?? defaultListState.isLoading;
  const fetchInitial = useListStore(state => state.fetchInitial);
  const loadMore = useListStore(state => state.loadMore);
  const removeItem = useListStore(state => state.removeItem);
  const listError = listState?.error ?? null;

  // ---- local state for the media/combined category -----------------------
  const [refreshedImageItems, setRefreshedImageItems] = useState<MediaPreview[] | null>(null);
  const [mediaState, setMediaState] = useState<MediaSearchState>(emptyMediaState);
  const [orderBy, setOrderBy] = useState<"relevance" | "date">("relevance");
  const [retryTick, setRetryTick] = useState(0);
  const revisionRef = useRef(new ListRevision());
  const refreshLocalRef = useRef<(prefix: string) => void>(() => {});
  useEffect(() => mutationBus.subscribe(event => {
    if (event.type === "list:invalidate") { refreshLocalRef.current(event.prefix); return; }
    if (event.type === "media:deleted") revisionRef.current.remove(event.ids);
    else if (event.type === "media:updated" || event.type === "media:moved") revisionRef.current.patch(event.items);
    else return;
    setMediaState(previous => ({ ...previous, media: revisionRef.current.reconcile(previous.media, 0, false) }));
    setRefreshedImageItems(previous => revisionRef.current.reconcile(previous ?? preloadedState?.items?.filter(isMedia) ?? [], 0, false));
  }), [preloadedState]);
  const activeQueryRef = useRef(0);
  const paginationPendingRef = useRef(false);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [retryWaiting, setRetryWaiting] = useState(false);
  const resultsGridRef = useRef<HTMLDivElement>(null);
  const { isSelecting, selectedIds, setSelected, beginSelecting, clear } = useSelection();

  // Initial load for the combined (media) category
  useEffect(() => {
    const generation = ++activeQueryRef.current;
    const revision = revisionRef.current.revision;
    paginationPendingRef.current = false;
    if (retryTimerRef.current !== null) clearTimeout(retryTimerRef.current);
    setRetryWaiting(false);
    setRefreshedImageItems(null);
    if (category !== "media" || isImageSearch) return;
    if (!query) {
      setMediaState(emptyMediaState);
      return;
    }

    setMediaState({ ...emptyMediaState, isLoading: true });
    searchCombined(query, ITEMS_PER_PAGE, undefined, orderBy)
      .then((result) => {
        if (activeQueryRef.current !== generation) return;
        setMediaState({
          persons: result.persons ?? [],
          media: revisionRef.current.reconcile(result.media ?? [], revision, true),
          nextCursor: result.next_cursor,
          isLoading: false,
          hasMore: result.next_cursor !== null,
          error: null,
        });
      })
      .catch(() => {
        if (activeQueryRef.current !== generation) return;
        setMediaState({
          ...emptyMediaState,
          error: "Search failed. Please try again.",
        });
      });
    return () => { activeQueryRef.current += 1; };
  }, [category, query, location.key, isImageSearch, orderBy, retryTick]);

  const loadMoreMedia = useCallback((retry = false) => {
    if (!mediaState.hasMore || mediaState.isLoading || !mediaState.nextCursor ||
      paginationPendingRef.current || (mediaState.error && !retry)) return;
    paginationPendingRef.current = true;
    const cursor = mediaState.nextCursor;
    const generation = activeQueryRef.current;
    const revision = revisionRef.current.revision;
    setMediaState((prev) => ({ ...prev, isLoading: true, error: null }));
    searchCombined(query, ITEMS_PER_PAGE, cursor, orderBy)
      .then((result) => {
        if (activeQueryRef.current !== generation) return;
        setMediaState((prev) => ({
          ...prev,
          media: mergeUnique(prev.media, revisionRef.current.reconcile(result.media ?? [], revision, false)),
          nextCursor: result.next_cursor,
          isLoading: false,
          hasMore: result.next_cursor !== null,
          error: null,
        }));
      })
      .catch(() => {
        if (activeQueryRef.current !== generation) return;
        setMediaState((prev) => ({ ...prev, isLoading: false, error: "Could not load more results. Retry to continue." }));
      }).finally(() => {
        if (activeQueryRef.current === generation) paginationPendingRef.current = false;
      });
  }, [mediaState.hasMore, mediaState.isLoading, mediaState.nextCursor, mediaState.error, query, orderBy]);

  const retrySearch = () => {
    if (retryWaiting) return;
    setRetryWaiting(true);
    const generation = activeQueryRef.current;
    retryTimerRef.current = setTimeout(() => {
      if (activeQueryRef.current !== generation) return;
      setRetryWaiting(false);
      if (category !== "media") { void refreshCachedList(listKey); return; }
      if (isImageSearch) { refreshLocalRef.current(""); return; }
      if (mediaState.nextCursor) loadMoreMedia(true);
      else setRetryTick(tick => tick + 1);
    }, 750);
  };
  useEffect(() => () => {
    if (retryTimerRef.current !== null) clearTimeout(retryTimerRef.current);
  }, []);

  // ---- list-store fetch for tag / scene ----------------------------------
  useEffect(() => {
    if (!listKey) return;
    const fetcherMap = {
      tag: () => searchTags(query, ITEMS_PER_PAGE),
      scene: () => searchScenes(query, ITEMS_PER_PAGE),
    } as const;
    const fetcher = fetcherMap[category as "tag" | "scene"];
    if (fetcher) fetchInitial(listKey, fetcher);
  }, [listKey, category, query, fetchInitial, location.key]);

  // ---- infinite scroll ---------------------------------------------------
  const { ref: loaderRef, inView } = useInView({ threshold: 0.5 });

  useEffect(() => {
    if (!inView) return;
    if (category === "media") {
      loadMoreMedia();
    } else if (listHasMore && !listIsLoading && !listError) {
      const fetcherMap = {
        tag: (cursor?: string) => searchTags(query, ITEMS_PER_PAGE, cursor),
        scene: (cursor?: string) => searchScenes(query, ITEMS_PER_PAGE, cursor),
      } as const;
      const fetcher = fetcherMap[category as "tag" | "scene"];
      if (fetcher) loadMore(listKey, fetcher);
    }
  }, [inView, category, listHasMore, listIsLoading, listError, listKey, query, loadMore, loadMoreMedia]);

  // ---- derive visible items ----------------------------------------------
  const isLoading = category === "media" ? mediaState.isLoading : listIsLoading;
  const hasMore = category === "media" ? mediaState.hasMore : listHasMore;

  const displayItems = (preloadedState?.items || listItems) as (
    | MediaPreview
    | Person
    | Tag
    | SceneSearchResult
  )[];

  const rawMediaItems: MediaPreview[] =
    category === "media"
      ? isImageSearch
        ? (refreshedImageItems ?? preloadedState?.items?.filter(isMedia) ?? [])
        : mediaState.media
      : [];

  const [mediaFilter, setMediaFilter] = useState<MediaFilter>("all");
  const filteredMediaItems = useMemo(() => {
    if (mediaFilter === "all") return rawMediaItems;
    if (mediaFilter === "video") return rawMediaItems.filter(isVideoMedia);
    return rawMediaItems.filter((i) => !isVideoMedia(i));
  }, [mediaFilter, rawMediaItems]);

  const { marqueeRect, onItemClick } = useGridSelection<number>({
    listKey: `search:${category}:${query}:${orderBy}:${mediaFilter}:${isImageSearch ? location.key : ""}`,
    loadedCount: filteredMediaItems.length,
    hasMore,
    containerRef: resultsGridRef,
    itemSelector: "[data-media-card]",
    getId: (element) => Number(element.dataset.selectableId),
    disabled: category !== "media",
    selecting: isSelecting,
    allowPlainDragOnItems: false,
    onEnterSelection: beginSelecting,
    onExitSelection: clear,
    selectedIds,
    onSelectionChange: setSelected,
  });

  const refreshSearch = async () => {
    const generation = ++activeQueryRef.current;
    const revision = revisionRef.current.revision;
    paginationPendingRef.current = false;
    if (category !== "media") { await refreshCachedList(listKey); return; }
    setMediaState(previous => ({ ...previous, isLoading: true, error: null }));
    try {
      if (isImageSearch) {
        const previews = await Promise.all(rawMediaItems.map(async media => {
          try {
            const detail = await getMedia(String(media.id));
            return { ...media, ...detail.media, thumbnail_path: detail.media.thumbnail_path ?? media.thumbnail_path };
          } catch (error) {
            // Count-only mutation responses require re-reading membership. One
            // removed image must not prevent the surviving previews refreshing.
            if (error && typeof error === "object" && "status" in error && error.status === 404) return null;
            throw error;
          }
        }));
        if (generation !== activeQueryRef.current) return;
        setRefreshedImageItems(revisionRef.current.reconcile(previews.filter((media): media is NonNullable<typeof media> => media !== null), revision, true));
        return;
      }
      const result = await searchCombined(query, ITEMS_PER_PAGE, undefined, orderBy);
      if (generation !== activeQueryRef.current) return;
      setMediaState({ persons: result.persons ?? [], media: revisionRef.current.reconcile(result.media ?? [], revision, true),
        nextCursor: result.next_cursor, hasMore: result.next_cursor !== null, isLoading: false, error: null });
    } catch (error) {
      if (generation === activeQueryRef.current) setMediaState(previous => ({ ...previous,
        error: error instanceof Error ? error.message : "Search refresh failed. Please try again." }));
      throw error;
    } finally {
      if (generation === activeQueryRef.current) setMediaState(previous => ({ ...previous, isLoading: false }));
    }
  };
  useUndoRefresh(`search:${category}:${query}`, refreshSearch);
  refreshLocalRef.current = prefix => {
    if (category !== "media" || !(prefix === "" || `search:${category}:${query}`.startsWith(prefix))) return;
    // Errors are rendered by refreshSearch; the mutation has already succeeded.
    void refreshSearch().catch(() => {});
  };

  const navigationContext = useMemo(
    () =>
      category === "media"
        ? { ids: filteredMediaItems.map((i) => i.id) }
        : undefined,
    [category, filteredMediaItems]
  );

  const visibleItems: (MediaPreview | Person | Tag | SceneSearchResult)[] =
    category === "media" ? filteredMediaItems : displayItems;

  const hasResults = visibleItems.length > 0;
  const shouldShowWarmup =
    !hasResults && isLoading && (category === "media" || category === "scene");

  const [showModelWarmup, setShowModelWarmup] = useState(false);
  useEffect(() => {
    setShowModelWarmup(false);
    if (!shouldShowWarmup) return;
    const timer = window.setTimeout(() => setShowModelWarmup(true), 700);
    return () => window.clearTimeout(timer);
  }, [shouldShowWarmup, listKey, query]);

  // ---- render ------------------------------------------------------------
  const title = isImageSearch
    ? "Similar Image Results"
    : `Search Results for "${query}"`;

  const handleTagDeleted = (tagId: number) => removeItem(listKey, tagId);

  const renderItem = (
    item: MediaPreview | Person | Tag | SceneSearchResult
  ) => {
    const itemKey =
      category === "scene"
        ? `scene-${(item as SceneSearchResult).scene_id}`
        : `${category}-${(item as MediaPreview | Person | Tag).id}`;

    if (isMedia(item)) {
      return (
        <div key={itemKey}>
          <MediaCard
            media={item}
            mediaListKey={listKey}
            navigationContext={navigationContext}
            onSelectionClick={onItemClick}
          />
        </div>
      );
    }
    if (isTag(item)) {
      return (
        <div key={itemKey}>
          <TagCard onTagDeleted={handleTagDeleted} tag={item} />
        </div>
      );
    }
    if (category === "scene") {
      return (
        <div key={itemKey}>
          <SceneResultCard scene={item as SceneSearchResult} listKey={listKey} />
        </div>
      );
    }
    return null;
  };

  return (
    <Container maxWidth="xl" sx={{ pt: 4, pb: 6 }}>
      <Typography variant="h4" gutterBottom>
        {title}
      </Typography>

      {category === "media" && (
        <Box sx={{ mb: 3, display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}>
          <ToggleButtonGroup
            exclusive
            size="small"
            value={mediaFilter}
            onChange={(_, next) => { if (next) setMediaFilter(next); }}
          >
            <ToggleButton value="all">All</ToggleButton>
            <ToggleButton value="image">Images</ToggleButton>
            <ToggleButton value="video">Videos</ToggleButton>
          </ToggleButtonGroup>

          {!isImageSearch && (
            <ToggleButtonGroup
              exclusive
              size="small"
              value={orderBy}
              onChange={(_, next) => { if (next) setOrderBy(next); }}
            >
              <ToggleButton value="relevance">Relevance</ToggleButton>
              <ToggleButton value="date">Newest first</ToggleButton>
            </ToggleButtonGroup>
          )}
        </Box>
      )}

      {(category === "media" ? mediaState.error : listError) && (
        <Alert
          severity="error"
          sx={{ mb: 3 }}
          action={
            <Button
              color="inherit"
              size="small"
              disabled={retryWaiting}
              onClick={retrySearch}
            >
              Retry
            </Button>
          }
        >
          {category === "media" ? mediaState.error : listError}
        </Alert>
      )}

      {showModelWarmup && (
        <Box sx={{ mb: 3, display: "flex", alignItems: "center", gap: 2, color: "text.secondary" }}>
          <CircularProgress size={20} />
          <Typography>
            Warming up AI models for search. The first search can take a minute.
          </Typography>
        </Box>
      )}

      {/* Person strip — shown on first page of media searches when names are detected */}
      {category === "media" && !isImageSearch && mediaState.persons.length > 0 && (
        <Box sx={{ mb: 3 }}>
          <Typography
            variant="overline"
            color="text.secondary"
            sx={{ display: "block", mb: 1, fontWeight: 700 }}
          >
            People
          </Typography>
          <Box
            sx={{
              display: "flex",
              gap: 2,
              overflowX: "auto",
              pb: 1,
              "&::-webkit-scrollbar": { height: 4 },
              "&::-webkit-scrollbar-thumb": { borderRadius: 2, bgcolor: "divider" },
            }}
          >
            {mediaState.persons.map((person) => (
              <Box key={person.id} sx={{ width: 130, flexShrink: 0 }}>
                <PersonCard person={person} />
              </Box>
            ))}
          </Box>
        </Box>
      )}

      <Box ref={resultsGridRef} sx={{ position: "relative" }}>
        <Masonry
          breakpointCols={breakpointColumnsObj}
          className="my-masonry-grid"
          columnClassName="my-masonry-grid_column"
        >
          {visibleItems.map(renderItem)}
        </Masonry>
        <MarqueeSelectionBox
          container={resultsGridRef.current}
          rect={marqueeRect}
        />
      </Box>

      {isLoading && (
        <Box textAlign="center" py={4}>
          <CircularProgress />
        </Box>
      )}
      {hasMore && !isLoading && <Box ref={loaderRef} sx={{ height: "1px" }} />}
      {!isLoading &&
        visibleItems.length === 0 &&
        !(category === "media" ? mediaState.error : listError) && (
          <Typography sx={{ mt: 4 }}>No results found.</Typography>
        )}
    </Container>
  );
}
