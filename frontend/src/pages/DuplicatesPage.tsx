import GalleryPage from "../components/GalleryPage";
import { useMutationRefresh, useUndoRefresh } from "../context/UndoContext";
import ListStateView from "../components/ListState";
import { useListInvalidation } from "../stores/useListStore";
import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  VariableSizeList,
  type ListChildComponentProps,
  type ListOnItemsRenderedProps,
} from "react-window";
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
import { getDuplicates, getDuplicateStats, resolveDuplicates } from "../services/duplicates";
import { createDuplicateResolver, pruneDuplicateGroups, type DuplicateResolveSnapshot } from "../services/duplicateResolver";
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
  measureRow: (index: number, groupId: number, height: number) => void;
}

function GroupRow({ index, style, data }: ListChildComponentProps<GroupRowData>) {
  const rowRef = useRef<HTMLDivElement>(null);
  const group = data.groups[index];
  const { measureRow } = data;

  useLayoutEffect(() => {
    const element = rowRef.current;
    if (!element || !group) return;
    // Measure natural content, not the absolutely positioned row's assigned height.
    const measure = () => measureRow(index, group.group_id, Math.ceil(element.getBoundingClientRect().height));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [index, group, measureRow]);

  return (
    <div style={style}>
      {group ? (
        <Box ref={rowRef} sx={{
          // Included in ResizeObserver measurement, so virtual offsets include
          // clearance for the card border and its outward focus outline.
          pt: 0.5,
          px: 0.5,
          pb: 1,
          // Each group's action header sticks within the virtual list's own
          // scrollport, then leaves with its group. Page filters stay above it.
          "& > .MuiPaper-root > .MuiBox-root:first-of-type": {
            position: "sticky",
            top: 0,
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
      ) : (
        <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 1, height: LOADER_HEIGHT }}>
          <CircularProgress size={20} />
          <Typography variant="body2">Loading more groups...</Typography>
        </Box>
      )}
    </div>
  );
}

const groupRowKey = (index: number, data: GroupRowData) => data.groups[index]?.group_id ?? "loader";

const DuplicatesPage: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const folder = searchParams.get("folder") || null;
  const { isSelecting, selectedIds, setSelected, beginSelecting, clear } = useSelection();
  const gridRef = useRef<HTMLDivElement>(null);
  const headerRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<VariableSizeList<GroupRowData>>(null);
  const rowHeights = useRef(new Map<number, number>());
  const [viewport, setViewport] = useState({ width: 0, height: 480 });
  const [visibleStopIndex, setVisibleStopIndex] = useState(-1);
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
  const scrollToGroup = useCallback((index: number) => listRef.current?.scrollToItem(index, "smart"), []);
  const { focusedId, setFocusedId } = useDuplicateGroupFocus(groupIds, listKey, scrollToGroup);
  const fetchInitial = useListStore(state => state.fetchInitial);
const loadMore = useListStore(state => state.loadMore);
const removeItem = useListStore(state => state.removeItem);
const clearList = useListStore(state => state.clearList);
  const { marqueeRect, onItemClick } = useGridSelection({
    listKey,
    hasMore,
    containerRef: gridRef,
    selecting: isSelecting,
    selectedIds,
    onSelectionChange: setSelected,
    onEnterSelection: beginSelecting,
    onExitSelection: clear,
    allowPlainDragOnItems: false,
  });
  useListInvalidation(listKey);
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

  useEffect(() => {
    clearList(listKey);
    fetchInitial(listKey, () => getDuplicates(null, sortBy, mt, 10, minCount, folder));
    setVisibleStopIndex(-1);
    rowHeights.current.clear();
    listRef.current?.scrollTo(0);
  }, [fetchInitial, listKey, clearList, refreshKey, sortBy, mt, minCount, folder]);

  // A visible loader row requests the next cursor page; the effect runs again
  // when a short page arrives and still does not fill the viewport.
  useEffect(() => {
    if (hasMore && !isLoading && !listError && (groups.length === 0 || visibleStopIndex >= groups.length)) {
      loadMore(listKey, (cursor) => getDuplicates(cursor, sortBy, mt, 10, minCount, folder));
    }
  }, [visibleStopIndex, groups.length, hasMore, isLoading, listError, loadMore, listKey, sortBy, mt, minCount, folder]);

  useLayoutEffect(() => {
    const element = viewportRef.current;
    const header = headerRef.current;
    if (!element || !header) return;
    const measure = () => {
      const width = element.clientWidth;
      const height = Math.max(240, window.innerHeight - element.getBoundingClientRect().top - (isSelecting ? 112 : 16));
      setViewport((previous) => previous.width === width && previous.height === height ? previous : { width, height });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    observer.observe(header);
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [isSelecting]);

  useLayoutEffect(() => {
    // Offscreen measurements are invalid after responsive columns change.
    rowHeights.current.clear();
    listRef.current?.resetAfterIndex(0);
  }, [viewport.width]);

  useLayoutEffect(() => {
    // Resolved/removed groups shift indices; heights remain keyed by group ID.
    listRef.current?.resetAfterIndex(0);
  }, [groups]);

  const measureRow = useCallback((index: number, groupId: number, height: number) => {
    if (height > 0 && rowHeights.current.get(groupId) !== height) {
      rowHeights.current.set(groupId, height);
      listRef.current?.resetAfterIndex(index);
    }
  }, []);

  const getRowHeight = (index: number) => {
    const group = groups[index];
    return group ? rowHeights.current.get(group.group_id) ?? ESTIMATED_GROUP_HEIGHT : LOADER_HEIGHT;
  };

  const handleItemsRendered = useCallback(({ visibleStopIndex }: ListOnItemsRenderedProps) => {
    setVisibleStopIndex(visibleStopIndex);
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
  const resolverState = useRef({ handleGroupResolved, listKey, sortBy, mt, minCount, folder });
  resolverState.current = { handleGroupResolved, listKey, sortBy, mt, minCount, folder };
  const resolver = useMemo(() => createDuplicateResolver({
    resolve: resolveDuplicates,
    removeGroup: (snapshot) => resolverState.current.handleGroupResolved(snapshot.groupId),
    onSettled: () => setStatsVersion((previous) => previous + 1),
    onError: (snapshot, error) => setResolveError(`Group ${snapshot.groupId} was not resolved: ${error.message}`),
    refresh: () => {
      const current = resolverState.current;
      useListStore.getState().clearList(current.listKey);
      void useListStore.getState().fetchInitial(current.listKey, () =>
        getDuplicates(null, current.sortBy, current.mt, 10, current.minCount, current.folder));
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
    fetchInitial(listKey, () => getDuplicates(null, sortBy, mt, 10, minCount, folder));
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
  };

  return (
    <GalleryPage title="Potential Duplicates" count={duplicateGroups.length}>
      <Box ref={headerRef} sx={{ pb: 2 }}>
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
            <Select
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
            <Select
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
            <Select
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
      <Box ref={viewportRef} sx={{ position: "relative", overflow: "hidden", height: viewport.height }}>
        {groups.length > 0 && viewport.width > 0 ? (
          <VariableSizeList<GroupRowData>
            ref={listRef}
            outerRef={gridRef}
            width={viewport.width}
            height={viewport.height}
            itemCount={groups.length + (hasMore ? 1 : 0)}
            itemSize={getRowHeight}
            estimatedItemSize={ESTIMATED_GROUP_HEIGHT}
            itemKey={groupRowKey}
            itemData={rowData}
            overscanCount={1}
            onItemsRendered={handleItemsRendered}
          >
            {GroupRow}
          </VariableSizeList>
        ) : isLoading ? (
          <ListStateView loading />
        ) : !listError && !hasMore ? (
          <ListStateView empty emptyMessage="No duplicates found." />
        ) : null}
        {/* The overlay stays outside scrolling content so scrollTop does not offset the marquee. */}
        <MarqueeSelectionBox container={viewportRef.current} rect={marqueeRect} />
      </Box>
      <Snackbar open={resolveError !== null} autoHideDuration={8000} onClose={() => setResolveError(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}>
        <Alert severity="error" variant="filled" onClose={() => setResolveError(null)}>{resolveError}</Alert>
      </Snackbar>
    </GalleryPage>
  );
};

export default DuplicatesPage;
