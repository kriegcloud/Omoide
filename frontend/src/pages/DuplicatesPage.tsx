import GalleryPage from "../components/GalleryPage";
import { useMutationRefresh, useUndoRefresh } from "../context/UndoContext";
import ListStateView from "../components/ListState";
import { refreshCachedList, useListInvalidation } from "../stores/useListStore";
import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useWindowVirtualRows } from "../hooks/useWindowVirtualRows";
import { useObservedHeight } from "../hooks/useObservedHeight";
import { infiniteScrollObserver } from "../hooks/infiniteScroll";
import {
  Typography,
  Box,
  CircularProgress,
  Alert,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Snackbar,
} from "@mui/material";
import { useListStore, defaultListState, type ListState } from "../stores/useListStore";
import { getDuplicates, getDuplicatesThrough, getDuplicateStats, resolveDuplicates } from "../services/duplicates";
import { createDuplicateResolver, duplicateListNeedsRefresh, pruneDuplicateGroups, type DuplicateResolveSnapshot } from "../services/duplicateResolver";
import { mutationBus } from "../stores/mutationBus";
import { DuplicateGroup } from "../components/DuplicateGroup";
import type { DuplicateGroup as GroupType, DuplicateStats } from "../types";
import { useTaskCompletionVersion, useTaskEvents } from "../TaskEventsContext";
import { formatBytes } from "../formatUtils";
import { useSelection } from "../context/SelectionContext";
import { useGridSelection } from "../hooks/useMarqueeSelection";
import MarqueeSelectionBox from "../components/MarqueeSelectionBox";
import { useSearchParams } from "react-router-dom";
import FolderFilterSelect from "../components/FolderFilterSelect";

import { useDuplicateGroupFocus } from "../hotkeys/useDuplicateGroupFocus";

const ESTIMATED_GROUP_HEIGHT = 480;
const LOADER_HEIGHT = 64;

interface GroupRowData {
  groups: GroupType[];
  focusedId: number | null;
  onFocusGroup: (id: number) => void;
  selecting: boolean;
  selectedIds: Set<number>;
  masterIds: Record<number, number>;
  onSelectMaster: (groupId: number, mediaId: number) => void;
  onSelectionClick: React.ComponentProps<typeof DuplicateGroup>["onSelectionClick"];
  onSelectGroup: (group: GroupType, checked: boolean) => void;
  onResolveRequested: (snapshot: DuplicateResolveSnapshot) => void;
  measureRow: (groupId: number, height: number) => void;
  headerOffset: number;
  measurementKey: number;
}

function GroupRow({ index, style, data }: { index: number; style: React.CSSProperties; data: GroupRowData }) {
  const rowRef = useRef<HTMLDivElement>(null);
  const group = data.groups[index];
  const { measureRow, measurementKey } = data;
  const groupId = group?.group_id;
  // Measure natural content, not the absolutely positioned row's assigned height.
  // Observer-only: on mount the nested Gallery still has its 1 px placeholder
  // layout, and caching that height made rows fight the scroll anchor.
  const onHeight = useCallback((height: number) => {
    if (groupId !== undefined) measureRow(groupId, height);
  }, [groupId, measureRow]);
  useObservedHeight(rowRef, onHeight, measurementKey);

  return (
    <div style={style}>
      {group && (
        <Box ref={rowRef} sx={{
          // Included in ResizeObserver measurement, so virtual offsets include
          // clearance for the card border and its outward focus outline.
          pt: 0.5,
          px: 0.5,
          pb: 1,
          // Stick below the measured app bar and leave with this group.
          "& > .MuiPaper-root > .MuiBox-root:first-of-type": {
            position: "sticky",
            top: data.headerOffset,
            zIndex: (theme) => theme.zIndex.appBar - 1,
            bgcolor: "background.paper",
            boxShadow: 1,
          },
        }}>
          <DuplicateGroup
            group={group}
            focused={data.focusedId === group.group_id}
            onFocusGroup={() => data.onFocusGroup(group.group_id)}
            selecting={data.selecting}
            selectedIds={data.selectedIds}
            masterId={group.items.find((media) => media.id === data.masterIds[group.group_id])?.id ?? group.items[0].id}
            onSelectMaster={(mediaId) => data.onSelectMaster(group.group_id, mediaId)}
            onSelectionClick={data.onSelectionClick}
            onSelectGroup={(checked) => data.onSelectGroup(group, checked)}
            onResolveRequested={data.onResolveRequested}
          />
        </Box>
      )}
    </div>
  );
}

const DuplicatesPage: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const folder = searchParams.get("folder") || null;
  const { isSelecting, selectedIds, setSelected, beginSelecting, clear } = useSelection();
  const gridRef = useRef<HTMLDivElement>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);
  const [rowHeights, setRowHeights] = useState(new Map<number, number>());
  const [layoutWidth, setLayoutWidth] = useState(0);
  const measuredWidth = useRef(0);
  const [headerOffset, setHeaderOffset] = useState(0);
  const [nearEnd, setNearEnd] = useState(false);
  const [masterIds, setMasterIds] = useState<Record<number, number>>({});
  const [sortBy, setSortBy] = useState<"count" | "size">("count");
  const [mediaType, setMediaType] = useState<"" | "image" | "video">("");
  const [minCount, setMinCount] = useState<number>(2);

  const listKey = useMemo(
    () => `duplicate-groups-${sortBy}-${mediaType}-${minCount}-${encodeURIComponent(folder ?? "")}`,
    [sortBy, mediaType, minCount, folder]
  );

  const {
    items: duplicateGroups,
    hasMore,
    isLoading,
    error: listError,
  } = useListStore((state) => state.lists[listKey] || defaultListState) as ListState<GroupType>;
  const groups = useMemo(() => duplicateGroups.filter((group) => group.items.length > 1), [duplicateGroups]);
  const groupIds = useMemo(() => groups.map(group => group.group_id), [groups]);
  const measuredHeights = useMemo(() => new Map(groups.map((group, index) =>
    [index, rowHeights.get(group.group_id) ?? ESTIMATED_GROUP_HEIGHT])), [groups, rowHeights]);
  const { totalHeight, items: virtualRows, scrollToIndex } = useWindowVirtualRows({
    containerRef: gridRef,
    rowCount: groups.length,
    estimatedHeight: ESTIMATED_GROUP_HEIGHT,
    measuredHeights,
    overscan: window.innerHeight * 1.5,
    headerOffset,
    layoutKey: `${listKey}:${layoutWidth}:${groupIds.join(",")}`,
  });
  const scrollToGroup = useCallback((index: number) => scrollToIndex(index, "smart"), [scrollToIndex]);
  const { focusedId, setFocusedId } = useDuplicateGroupFocus(groupIds, listKey, scrollToGroup);
  const fetchInitial = useListStore(state => state.fetchInitial);
const loadMore = useListStore(state => state.loadMore);
const removeItem = useListStore(state => state.removeItem);
const clearList = useListStore(state => state.clearList);
  const { marqueeRect, onItemClick } = useGridSelection({
    listKey,
    hasMore,
    containerRef: gridRef,
    virtualized: true,
    orderedIds: groups.flatMap(group => group.items.map(media => media.id)),
    loadedCount: new Set(groups.flatMap(group => group.items.map(media => media.id))).size,
    selecting: isSelecting,
    selectedIds,
    onSelectionChange: setSelected,
    onEnterSelection: beginSelecting,
    onExitSelection: clear,
    allowPlainDragOnItems: false,
  });
  useListInvalidation(listKey, duplicateListNeedsRefresh);
  const refreshKey = useTaskCompletionVersion(["find_duplicates", "generate_hashes"]);
  const { activeTasks } = useTaskEvents();
  const duplicateTask = activeTasks.find(
    (task) => task.task_type === "find_duplicates"
  );

  const hashTask = activeTasks.find(
    (task) => task.task_type === "generate_hashes"
  );

  const [stats, setStats] = useState<DuplicateStats | null>(null);
  const [isLoadingStats, setIsLoadingStats] = useState(false);
  const [statsError, setStatsError] = useState<string | null>(null);
  const [statsVersion, setStatsVersion] = useState(0);
  useMutationRefresh(["media:deleted", "media:moved", "duplicates:resolved", "list:invalidate"], () => { setStatsVersion(value => value + 1); });
  useUndoRefresh("duplicates-stats", async () => { setStats(await getDuplicateStats(folder)); });

  const mt = mediaType || undefined;
  const headerOffsetRef = useRef(headerOffset);
  headerOffsetRef.current = headerOffset;

  // The registered initial fetcher is what refreshCachedList re-runs, so size it
  // to the loaded depth (10 for a fresh list) rather than one 10-group page.
  const firstPage = useCallback(() => getDuplicatesThrough(
    useListStore.getState().lists[listKey]?.items.length ?? 0, sortBy, mt, 10, minCount, folder,
  ), [listKey, sortBy, mt, minCount, folder]);

  useEffect(() => {
    clearList(listKey);
    fetchInitial(listKey, firstPage);
    setRowHeights(new Map());
    const element = gridRef.current;
    if (element) window.scrollTo({ top: Math.max(0, element.getBoundingClientRect().top + window.scrollY - headerOffsetRef.current), behavior: "instant" });
  }, [fetchInitial, listKey, clearList, refreshKey, firstPage]);

  // Match other galleries: prefetch when the end is within 1.5 window heights.
  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;
    const observer = new IntersectionObserver(([entry]) => setNearEnd(entry.isIntersecting), infiniteScrollObserver);
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (hasMore && !isLoading && !listError && nearEnd) {
      loadMore(listKey, (cursor) => getDuplicates(cursor, sortBy, mt, 10, minCount, folder));
    }
  }, [nearEnd, groups.length, hasMore, isLoading, listError, loadMore, listKey, sortBy, mt, minCount, folder]);

  useLayoutEffect(() => {
    const element = gridRef.current;
    const appBar = document.querySelector<HTMLElement>(".MuiAppBar-positionSticky");
    if (!element) return;
    const measure = () => {
      const width = element.clientWidth;
      if (measuredWidth.current !== width) {
        measuredWidth.current = width;
        setRowHeights(new Map());
        setLayoutWidth(width);
      }
      setHeaderOffset(appBar?.getBoundingClientRect().height ?? 0);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    if (appBar) observer.observe(appBar);
    window.addEventListener("resize", measure);
    return () => { observer.disconnect(); window.removeEventListener("resize", measure); };
  }, []);

  const measureRow = useCallback((groupId: number, height: number) => {
    if (height <= 0) return;
    setRowHeights(previous => previous.get(groupId) === height ? previous : new Map(previous).set(groupId, height));
  }, []);

  useEffect(() => {
    let isActive = true;
    setStats(null);
    setStatsError(null);

    const loadStats = async () => {
      setIsLoadingStats(true);
      try {
        const result = await getDuplicateStats(folder);
        if (isActive) {
          setStats(result);
          setStatsError(null);
        }
      } catch (error) {
        if (isActive) {
          const message =
            error instanceof Error
              ? error.message
              : "Failed to load duplicate statistics";
          setStatsError(message);
        }
      } finally {
        if (isActive) {
          setIsLoadingStats(false);
        }
      }
    };

    loadStats();

    return () => {
      isActive = false;
    };
  }, [refreshKey, folder, statsVersion]);

  // This handler will be passed down to remove a whole group from the UI once it's resolved
  const handleGroupResolved = (groupId: number) => {
    const group = duplicateGroups.find((candidate) => candidate.group_id === groupId);
    const next = new Set(selectedIds);
    for (const media of group?.items ?? []) next.delete(media.id);
    setSelected(next);
    removeItem(listKey, groupId);
    setStatsVersion((previous) => previous + 1);
  };

  // Optimistic resolution: the group leaves the list (and focus advances) the
  // moment the user confirms; the server works in the background. Latest
  // page state is read through a ref so the resolver is created once.
  const [resolvingCount, setResolvingCount] = useState(0);
  const [resolveError, setResolveError] = useState<string | null>(null);
  const resolverState = useRef({ handleGroupResolved, listKey });
  resolverState.current = { handleGroupResolved, listKey };
  const resolver = useMemo(() => createDuplicateResolver({
    resolve: resolveDuplicates,
    removeGroup: (snapshot) => resolverState.current.handleGroupResolved(snapshot.groupId),
    onSettled: () => setStatsVersion((previous) => previous + 1),
    onError: (snapshot, error) => setResolveError(`Group ${snapshot.groupId} was not resolved: ${error.message}`),
    // Re-read survivors in place (same depth); clearing collapsed the list to page 1.
    refresh: () => {
      void refreshCachedList(resolverState.current.listKey).catch(error => console.error("Duplicate refresh failed", error));
      setStatsVersion((previous) => previous + 1);
    },
    onPendingChange: setResolvingCount,
  }), []);

  // Media deleted anywhere (e.g. the selection bar's "Delete files…") must
  // leave the loaded groups now, not after a refresh.
  useEffect(() => mutationBus.subscribe((event) => {
    if (event.type !== "media:deleted") return;
    const key = resolverState.current.listKey;
    const list = useListStore.getState().lists[key];
    if (!list) return;
    const { groups: pruned, changed } = pruneDuplicateGroups(list.items as GroupType[], event.ids);
    if (!changed) return;
    useListStore.setState((state) => ({
      lists: { ...state.lists, [key]: { ...state.lists[key], items: pruned } },
    }));
    setStatsVersion((previous) => previous + 1);
  }), []);

  const handleRetryLoad = () => {
    clearList(listKey);
    fetchInitial(listKey, firstPage);
  };

  const formatNumber = (value: number) => value.toLocaleString();

  const rowData: GroupRowData = {
    groups,
    focusedId,
    onFocusGroup: setFocusedId,
    selecting: isSelecting,
    selectedIds,
    masterIds,
    onSelectMaster: (groupId, mediaId) => setMasterIds((previous) => ({ ...previous, [groupId]: mediaId })),
    onSelectionClick: onItemClick,
    onSelectGroup: (group, checked) => {
      const next = new Set(selectedIds);
      for (const media of group.items) {
        if (checked) next.add(media.id);
        else next.delete(media.id);
      }
      beginSelecting();
      setSelected(next);
    },
    onResolveRequested: resolver.request,
    measureRow,
    measurementKey: layoutWidth,
    headerOffset,
  };

  return (
    <GalleryPage title="Potential Duplicates" count={duplicateGroups.length}>
      <Box sx={{ pb: 2 }}>
        <Box sx={{ display: "flex", alignItems: "baseline", gap: 2, flexWrap: "wrap", mb: 1 }}>

          <Typography variant="caption" color="text.secondary">
            Selection covers loaded rows only
          </Typography>
          {resolvingCount > 0 && (
            <Typography variant="caption" color="primary" role="status" aria-live="polite"
              sx={{ display: "inline-flex", alignItems: "center", gap: 0.75 }}>
              <CircularProgress size={10} thickness={6} color="inherit" />
              Resolving {resolvingCount} {resolvingCount === 1 ? "group" : "groups"}…
            </Typography>
          )}
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}>
          <Box sx={{ flexGrow: 1 }}>
            {stats ? (
              <Typography variant="body2" color="text.secondary">
                <Box component="span" sx={{ fontWeight: "bold", color: "text.primary" }}>
                  {formatNumber(stats.total_groups)} groups · {formatNumber(stats.total_items)} files
                </Box>
                {" · "}{formatBytes(stats.total_size_bytes)} total · {formatBytes(stats.total_reclaimable_bytes)} reclaimable
              </Typography>
            ) : isLoadingStats ? (
              <Typography variant="body2" color="text.secondary">Loading duplicate statistics...</Typography>
            ) : null}
            {isLoadingStats && stats && (
              <Typography variant="caption" color="text.secondary">Refreshing statistics...</Typography>
            )}
          </Box>
          <FolderFilterSelect
            value={folder}
            onChange={(nextFolder) => {
              const next = new URLSearchParams(searchParams);
              if (nextFolder) next.set("folder", nextFolder);
              else next.delete("folder");
              setSearchParams(next);
            }}
          />
          <FormControl size="small" sx={{ minWidth: 140 }}>
            <InputLabel>Media Type</InputLabel>
            <Select inputProps={{ "aria-label": "Media Type" }}
              value={mediaType}
              label="Media Type"
              onChange={(e) => setMediaType(e.target.value as "" | "image" | "video")}
            >
              <MenuItem value="">All</MenuItem>
              <MenuItem value="image">Images</MenuItem>
              <MenuItem value="video">Videos</MenuItem>
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ minWidth: 160 }}>
            <InputLabel>Sort By</InputLabel>
            <Select inputProps={{ "aria-label": "Sort By" }}
              value={sortBy}
              label="Sort By"
              onChange={(e) => setSortBy(e.target.value as "count" | "size")}
            >
              <MenuItem value="count">Most Files</MenuItem>
              <MenuItem value="size">Largest Size</MenuItem>
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ minWidth: 160 }}>
            <InputLabel>Min. Copies</InputLabel>
            <Select inputProps={{ "aria-label": "Min. Copies" }}
              value={minCount}
              label="Min. Copies"
              onChange={(e) => setMinCount(Number(e.target.value))}
            >
              <MenuItem value={2}>Any (2+)</MenuItem>
              <MenuItem value={3}>3 or more</MenuItem>
              <MenuItem value={4}>4 or more</MenuItem>
              <MenuItem value={5}>5 or more</MenuItem>
            </Select>
          </FormControl>
        </Box>

        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
          Start duplicate detection from the header control panel; this list refreshes when a run completes.
        </Typography>
        {stats && (stats.type_breakdown.length > 0 || stats.top_folders.length > 0) && (
          <Box component="details" sx={{ mt: 1 }}>
            <Typography component="summary" variant="caption" sx={{ cursor: "pointer" }}>
              More statistics
            </Typography>
            {stats.type_breakdown.map((entry) => (
              <Typography key={entry.type} variant="body2" color="text.secondary">
                {entry.type === "image" ? "Images" : "Videos"}: {formatNumber(entry.items)} items · {formatNumber(entry.groups)} groups · {formatBytes(entry.size_bytes)}
              </Typography>
            ))}
            {stats.top_folders.length > 0 && (
              <Typography variant="subtitle2" sx={{ mt: 1 }}>Top Folders by Duplicates</Typography>
            )}
            {stats.top_folders.map((folder) => (
              <Typography key={folder.folder} variant="body2" color="text.secondary" sx={{ overflowWrap: "anywhere" }}>
                {folder.folder}: {formatNumber(folder.items)} items · {formatNumber(folder.groups)} groups · {formatBytes(folder.size_bytes)}
              </Typography>
            ))}
          </Box>
        )}
        {duplicateTask && (
          <Alert severity="warning" sx={{ mt: 1 }}>
            Duplicate detection running... {duplicateTask.processed}/{duplicateTask.total}
            {duplicateTask.current_step ? " (" + duplicateTask.current_step + ")" : ""}
          </Alert>
        )}
        {hashTask && (
          <Alert severity="info" sx={{ mt: 1 }}>
            Hash generation running... {hashTask.processed}/{hashTask.total}
            {hashTask.current_step ? " (" + hashTask.current_step + ")" : ""}
          </Alert>
        )}
        {statsError && <Alert severity="error" sx={{ mt: 1 }}>{statsError}</Alert>}
        <ListStateView error={listError} onRetry={handleRetryLoad} />
      </Box>
      <Box ref={gridRef} sx={{ position: "relative", height: totalHeight, overflowAnchor: "none" }}>
        {virtualRows.map(({ index, top }) => (
          <GroupRow key={groups[index].group_id} index={index} data={rowData}
            style={{ position: "absolute", top, left: 0, width: "100%" }} />
        ))}
        <MarqueeSelectionBox container={gridRef.current} rect={marqueeRect} />
      </Box>
      {/* Not a browser scroll anchor: anchoring to it dragged the window down
          with every appended page, which loaded the next page, to the end. */}
      <Box ref={sentinelRef} sx={{ minHeight: hasMore ? LOADER_HEIGHT : 1, overflowAnchor: "none" }}>
        {isLoading ? <ListStateView loading /> : !listError && !hasMore && groups.length === 0 ? (
          <ListStateView empty emptyMessage="No duplicates found." />
        ) : null}
      </Box>
      <Snackbar open={resolveError !== null} autoHideDuration={8000} onClose={() => setResolveError(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}>
        <Alert severity="error" variant="filled" onClose={() => setResolveError(null)}>{resolveError}</Alert>
      </Snackbar>
    </GalleryPage>
  );
};

export default DuplicatesPage;
