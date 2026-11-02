import GalleryPage from "../components/GalleryPage";
import React, { useCallback, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  FormControl,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Snackbar,
  Stack,
  Tabs,
  Tab,
  Typography,
} from "@mui/material";
import PersonOffIcon from "@mui/icons-material/PersonOff";
import ReplayIcon from "@mui/icons-material/Replay";
import { RerunProcessorsDialog } from "../components/RerunProcessorsDialog";
import { useTaskCompletionVersion } from "../TaskEventsContext";

import { NoPersonsMediaItem } from "../types";
import { getNoPersonsMedia, resolveNoPersons } from "../services/nopersons";
import { useCursorList } from "../hooks/useCursorList";
import { useSelection } from "../context/SelectionContext";
import { useGridSelection } from "../hooks/useMarqueeSelection";
import BulkResolveToolbar, {
  BulkResolveAction,
  FeedbackSeverity,
} from "../components/BulkResolveToolbar";
import { useSearchParams } from "react-router-dom";
import FolderFilterSelect from "../components/FolderFilterSelect";
import ReviewMediaGrid from "../components/ReviewMediaGrid";
import SelectableMediaTile from "../components/SelectableMediaTile";
import { formatBytes } from "../formatUtils";
import { getNoFacesMedia, resolveNoFaces } from "../services/nofaces";
import { runProcessor } from "../services/taskActions";
import { useHotkey } from "../hotkeys/useHotkey";
import { queryKey, entityKey } from "../stores/queryKeys";
import { startOperation } from "../stores/optimisticOperations";
import { mutationBus } from "../stores/mutationBus";

const PersonReviewPage: React.FC<{ kind: "nofaces" | "nopersons" }> = ({ kind }) => {
  const faces = kind === "nofaces";
  const [searchParams, setSearchParams] = useSearchParams();
  const folder = searchParams.get("folder") || null;
  const mediaType: "" | "image" | "video" = searchParams.get("media_type") === "image" ? "image" : searchParams.get("media_type") === "video" ? "video" : "";
  const scope = searchParams.get("scope") === (faces ? "all" : "pending") ? (faces ? "all" : "pending") : "processed";
  const setFilter = (name: string, value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(name, value); else next.delete(name);
    next.delete("item");
    setSearchParams(next);
  };
  const listKey = queryKey(kind, { folder, media_type: mediaType, scope });

  const [rerunDialogOpen, setRerunDialogOpen] = useState(false);
  const [rerunIds, setRerunIds] = useState<number[]>([]);
  const [snackbar, setSnackbar] = useState<{ open: boolean; message: string; severity: FeedbackSeverity }>({
    open: false, message: "", severity: "success",
  });

  const refreshKey = useTaskCompletionVersion([
    "process_media",
    "cluster_persons",
    "run_processor",
    "run_processor_for_media",
    "clean_missing_files",
  ]);

  const fetcher = useCallback(
    (cursor: string | null) => {
      const options = { cursor: cursor ?? undefined, limit: 50, folder, mediaType: mediaType || undefined };
      return faces
        ? getNoFacesMedia({ ...options, scope: scope === "all" ? "all" : "processed" })
        : getNoPersonsMedia({ ...options, scope: scope === "pending" ? "pending" : "processed" });
    },
    [mediaType, scope, folder, faces]
  );

  const {
    items,
    total,
    hasMore,
    isLoading,
    error,
    loaderRef,
    selectedIds,
    setSelectedIds,
    selectVisible,
    clearSelection,
    removeItems,
    refetch,
  } = useCursorList<NoPersonsMediaItem>(fetcher, refreshKey, { queryKey: listKey, entityKind: "media" });

  const gridRef = useRef<HTMLDivElement>(null);
  const { isSelecting: globalSelecting } = useSelection();
  const selecting = globalSelecting || selectedIds.size > 0;
  const { marqueeRect, onItemClick } = useGridSelection({
    containerRef: gridRef,
    selectedIds,
    onSelectionChange: setSelectedIds,
    selecting,
    allowPlainDragOnItems: false,
  });

  const showFeedback = useCallback(
    (message: string, severity: FeedbackSeverity) => setSnackbar({ open: true, message, severity }),
    []
  );

  const resolveSelection = useCallback(
    async ({ action, mediaIds, selectAll }: { action: BulkResolveAction; mediaIds?: number[]; selectAll?: boolean }) => {
      const ids = selectAll ? items.map(item => item.id) : mediaIds ?? [];
      const input = { action, folder, media_ids: selectAll ? undefined : ids, select_all: !!selectAll, media_type: (mediaType || undefined) as "image" | "video" | undefined, scope };
      startOperation({
        label: `${faces ? "No faces" : "No people"}: ${action.toLowerCase().replaceAll("_", " ")}`,
        input,
        resources: ids.map(id => entityKey("media", id)),
        patches: ids.map(id => ({ kind: "remove" as const, key: entityKey("media", id) })),
        request: async (payload, operationId) => {
          const result = await (faces
            ? resolveNoFaces({ ...payload, operation_id: operationId, scope: payload.scope === "all" ? "all" : "processed" })
            : resolveNoPersons({ ...payload, operation_id: operationId, scope: payload.scope === "pending" ? "pending" : "processed" }));
          const applied = (result.processed_ids ?? []).map(id => entityKey("media", id));
          return { operation_id: operationId, state: "succeeded", applied, skipped: ids.filter(id => !result.processed_ids?.includes(id)).map(id => ({ key: entityKey("media", id), reason: "Not processed" })), failed: [], unknown: [] };
        },
        receipt: result => ({ ...result, state: result.skipped.length ? "partial" : "succeeded" }),
        events: (_receipt, applied) => [{ type: "media:deleted", ids: applied.map(key => Number(key.split(":")[1])) }],
        refresh: async () => { await refetch(); mutationBus.emit({ type: "list:invalidate", prefix: kind }); },
      });
      clearSelection();
      return { removed: 0, processed_ids: [] };
    },
    [mediaType, scope, folder, faces, items, refetch, clearSelection, kind]
  );

  const runDetection = () => {
    void runProcessor("person_detection").then(
      () => showFeedback("Person detection started. Pending items will update when the task finishes.", "success"),
      error => showFeedback(error instanceof Error ? error.message : "Failed to start person detection", "error")
    );
  };
  useHotkey({ key: "r" }, () => {
    if (faces) { setRerunIds(selectedIds.size ? [...selectedIds] : items.map(item => item.id)); setRerunDialogOpen(true); }
    else runDetection();
  }, { scope: "page", description: faces ? "Rerun visible or selected media" : "Run person detection" });

  const handleResolved = useCallback(
    (_removedIds: number[], _removed: number, selectAll: boolean) => {
      // Successful service events reconcile processed ids across every list.
      if (selectAll) void refetch();
      clearSelection();
    },
    [refetch, clearSelection]
  );

  const handleAssigned = useCallback(
    (mediaIds: number[], skippedCount: number) => {
      if (skippedCount) {
        // The dialog exposes a count, so refetch to reconcile skipped items.
        void refetch();
      } else {
        removeItems(mediaIds);
      }
    },
    [refetch, removeItems]
  );

  const totalSize = items.reduce((sum, i) => sum + (i.size || 0), 0);
  const selectedCount = selectedIds.size;

  return (
    <GalleryPage title={faces ? "No faces" : "No people"} count={total}
      filters={<>
      <Paper variant="outlined" sx={{ p: 2, mb: 3 }}>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ sm: "center" }}>
          {faces && <FormControl size="small" sx={{ minWidth: 200 }}>
            <InputLabel>Scope</InputLabel>
            <Select
              label="Scope"
              value={scope}
              onChange={(e) => setFilter("scope", e.target.value)}
            >
              <MenuItem value="processed">Processed — no identified faces</MenuItem>
              <MenuItem value="all">All unlinked to any person</MenuItem>
            </Select>
          </FormControl>}
          <FormControl size="small" sx={{ minWidth: 140 }}>
            <InputLabel>Media type</InputLabel>
            <Select
              label="Media type"
              value={mediaType}
              onChange={(e) => setFilter("media_type", e.target.value)}
            >
              <MenuItem value="">All</MenuItem>
              <MenuItem value="image">Images</MenuItem>
              <MenuItem value="video">Videos</MenuItem>
            </Select>
          </FormControl>
        </Stack>
      </Paper>

      <Box sx={{ mb: 2 }}>
        <FolderFilterSelect
          value={folder}
          onChange={(nextFolder) => setFilter("folder", nextFolder ?? "")}
        />
      </Box>

      </>}
      tabs={!faces ? <Tabs value={scope} onChange={(_event, value) => setFilter("scope", value)} aria-label="Person detection scope"><Tab value="processed" label="Confirmed person-free" /><Tab value="pending" label="Pending analysis" /></Tabs> : undefined} selection={<BulkResolveToolbar
        deferredFeedback
        statsText={`${total.toLocaleString()} item${total !== 1 ? "s" : ""}`}
        shownCount={items.length}
        shownSize={totalSize}
        total={total}
        selectedIds={selectedIds}
        onSelectVisible={selectVisible}
        onClearSelection={clearSelection}
        resolve={resolveSelection}
        onResolved={handleResolved}
        onAssigned={handleAssigned}
        onFeedback={showFeedback}
        extraActions={faces ?
          <Button
            size="small"
            variant="outlined"
            startIcon={<ReplayIcon />}
            onClick={() => {
              const ids = selectedCount > 0 ? Array.from(selectedIds) : items.map((i) => i.id);
              setRerunIds(ids);
              setRerunDialogOpen(true);
            }}
            disabled={items.length === 0}
          >
            {selectedCount > 0 ? `Rerun (${selectedCount} selected)` : `Rerun all visible (${items.length})`}
          </Button> : <Button size="small" variant="outlined" startIcon={<ReplayIcon />} onClick={runDetection}>Run person detection</Button>
        }
      />}>

      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {faces ? "Media with no identified face association. The processed scope includes media where face detection ran without identifying a person; All unlinked also includes unprocessed media. Bodies or other person content may still be present." : "Confirmed person-free media has been analysed for people and body parts using person detection and semantic image matching. Pending items have not completed this analysis."}
        {" "}Click a thumbnail to open the full media view.
      </Typography>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}


      <ReviewMediaGrid
        gridRef={gridRef}
        marqueeRect={marqueeRect}
        error={error}
        onRetry={refetch}
        itemCount={items.length}
        isLoading={isLoading}
        hasMore={hasMore}
        loaderRef={loaderRef}
        empty={
          <>
            <PersonOffIcon sx={{ fontSize: 48, color: "text.disabled", mb: 1 }} />
            <Typography color="text.secondary">
              {faces ? (scope === "processed" ? "No processed media without identified faces." : "All media is linked to a person.") : scope === "pending" ? "No media awaiting person detection." : "No confirmed person-free media. Run person detection to analyse pending items."}
            </Typography>
          </>
        }
      >
        {items.map((item) => (
          <SelectableMediaTile
            key={item.id}
            id={item.id}
            filename={item.filename}
            href={`/medium/${item.id}?${new URLSearchParams({ ...Object.fromEntries(searchParams), item: String(item.id) })}`}
            thumbnailPath={item.thumbnail_path}
            selected={selectedIds.has(item.id)}
            selecting={selecting}
            onSelectionClick={onItemClick}
            badgeLabel={item.duration != null ? "video" : undefined}
            caption={faces ? formatBytes(item.size) : `${formatBytes(item.size)} · Person score ${item.person_score?.toFixed(3) ?? "pending"} · SigLIP margin ${item.person_siglip_margin?.toFixed(3) ?? "pending"}`}
          />
        ))}
      </ReviewMediaGrid>

      <RerunProcessorsDialog
        open={rerunDialogOpen}
        mediaIds={rerunIds}
        onClose={() => setRerunDialogOpen(false)}
        onStarted={() => showFeedback("Processing started.", "success")}
      />

      <Snackbar
        open={snackbar.open}
        autoHideDuration={5000}
        onClose={() => setSnackbar((p) => ({ ...p, open: false }))}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert onClose={() => setSnackbar((p) => ({ ...p, open: false }))} severity={snackbar.severity} sx={{ width: "100%" }}>
          {snackbar.message}
        </Alert>
      </Snackbar>
    </GalleryPage>
  );
};

export default PersonReviewPage;
