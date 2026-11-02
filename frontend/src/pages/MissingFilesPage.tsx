import { startReviewResolve } from "../services/review";
import { useOptimisticCachedList, refreshCachedList } from "../stores/useListStore";
import GalleryPage from "../components/GalleryPage";
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  FormControlLabel,
  Paper,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
  Checkbox,
} from "@mui/material";
import FilterAltIcon from "@mui/icons-material/FilterAlt";
import DoneAllIcon from "@mui/icons-material/DoneAll";
import UndoIcon from "@mui/icons-material/Undo";
import SelectAllIcon from "@mui/icons-material/SelectAll";
import ClearAllIcon from "@mui/icons-material/ClearAll";

import { MissingMediaItem, MissingSummaryEntry } from "../types";
import { API } from "../config";
import { encodeFilePath } from "../urlUtils";
import {
  confirmMissing,
  getMissingMedia,
  resetMissingFlags,
} from "../services/missing";
import { usePagedList } from "../hooks/usePagedList";
import { useIdSelection } from "../hooks/useIdSelection";
import { useTaskCompletionVersion, useTaskEvents } from "../TaskEventsContext";
import ConfirmDialog from "../components/ConfirmDialog";
import { formatBytes } from "../formatUtils";

const parseUtcDate = (iso: string) => new Date(iso.endsWith("Z") ? iso : `${iso}Z`);

const formatDuration = (iso: string | null) => {
  if (!iso) return "unknown";
  const date = parseUtcDate(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const diffMs = Date.now() - date.getTime();
  if (diffMs < 0) return date.toLocaleString();
  const diffHours = Math.floor(diffMs / 3_600_000);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 7) return `${diffDays}d ago`;
  const diffWeeks = Math.floor(diffDays / 7);
  if (diffWeeks < 8) return `${diffWeeks}w ago`;
  return date.toLocaleDateString();
};

const buildThumbUrl = (item: MissingMediaItem) => {
  const thumb = item.thumbnail_path
    ? encodeFilePath(item.thumbnail_path)
    : `${item.id}.jpg`;
  return `${API}/thumbnails/${thumb}`;
};

type MissingBulkKind = "confirm-selected" | "confirm-all" | "reset-all";

const MissingFilesPage: React.FC = () => {
  const [summary, setSummary] = useState<MissingSummaryEntry[]>([]);
  const [pendingBulk, setPendingBulk] = useState<{ kind: MissingBulkKind; ids: number[]; count: number; path: string; includeConfirmed: boolean; query: string } | null>(null);


  const [pathFilter, setPathFilter] = useState("");
  const [includeConfirmed, setIncludeConfirmed] = useState(false);
  // Typing in the path filter only reloads once the user pauses.
  const [debouncedPath, setDebouncedPath] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebouncedPath(pathFilter.trim()), 300);
    return () => clearTimeout(t);
  }, [pathFilter]);

  const { activeTasks } = useTaskEvents();
  const cleanupTask = activeTasks.find(
    (task) => task.task_type === "clean_missing_files"
  );

  const list = usePagedList<MissingMediaItem>(
    `missing:${includeConfirmed}:${debouncedPath}`,
    async (cursor, signal) => {
      const response = await getMissingMedia(
        {
          cursor,
          pathPrefix: debouncedPath || undefined,
          includeConfirmed,
        },
        signal
      );
      if (cursor === null) setSummary(response.summary);
      return response;
    },
    { infiniteScroll: false, refetchOnMount: true }
  );
  const listKey = `missing:${includeConfirmed}:${debouncedPath}`;
  const visibleList = useOptimisticCachedList<MissingMediaItem>(listKey);
  const { items, isLoading, error, hasMore } = visibleList ?? list;
  const { loadMore, reload: reloadList } = list;
  const totalMissing = (visibleList ?? list).total ?? 0;
  const selection = useIdSelection<number>();
  const { selectedIds, toggle: toggleSelection, clear: clearSelection } = selection;

  const reload = useCallback(() => {
    clearSelection();
    reloadList();
  }, [clearSelection, reloadList]);

  // A finished cleanup task changes which files are missing.
  const refreshKey = useTaskCompletionVersion(["clean_missing_files"]);
  const seenRefreshKey = useRef(refreshKey);
  useEffect(() => {
    if (seenRefreshKey.current === refreshKey) return;
    seenRefreshKey.current = refreshKey;
    reload();
  }, [refreshKey, reload]);

  // New filters mean a new list; the old selection no longer applies.
  useEffect(() => clearSelection(), [includeConfirmed, debouncedPath, clearSelection]);

  const selectAllVisible = () => selection.selectAll(items.map((item) => item.id));

  const requestBulk = (kind: MissingBulkKind) => {
    setPendingBulk({ kind, ids: [...selectedIds], count: kind === "confirm-selected" ? selectedIds.size : totalMissing,
      path: debouncedPath, includeConfirmed, query: listKey });
  };

  const submitMissing = (snapshot: { kind: MissingBulkKind | "reset-selected"; ids: number[]; path: string; includeConfirmed: boolean; query: string }) => {
    const all = snapshot.kind.endsWith("all");
    const reset = snapshot.kind.startsWith("reset");
    startReviewResolve({
      label: reset ? "Clear missing status" : "Remove missing records",
      ids: all ? [] : snapshot.ids,
      query: snapshot.query,
      countOnly: true,
      request: () => (reset ? resetMissingFlags : confirmMissing)(all
        ? { select_all: true, path_prefix: snapshot.path || undefined, include_confirmed: snapshot.includeConfirmed }
        : { media_ids: snapshot.ids }),
      refresh: () => refreshCachedList(snapshot.query),
    });
    selection.deselect(snapshot.ids);
  };

  const handleResetSelected = () => {
    if (!selectedIds.size) return;
    submitMissing({ kind: "reset-selected", ids: [...selectedIds], path: debouncedPath, includeConfirmed, query: listKey });
  };

  const selectedCount = selectedIds.size;

  const bulkCopy =
    pendingBulk?.kind === "confirm-selected"
      ? {
          title: "Remove records",
          message: `Remove ${pendingBulk.count} record(s) for missing files from the library. This cannot be undone.`,
          confirmLabel: "Remove records",
        }
      : pendingBulk?.kind === "confirm-all"
      ? {
          title: "Remove all filtered records",
          message: `Remove all ${pendingBulk.count.toLocaleString()} missing item(s) matching the current filters from the library. This cannot be undone.`,
          confirmLabel: "Remove records",
        }
      : pendingBulk?.kind === "reset-all"
      ? {
          title: "Clear missing status",
          message: `Clear the missing status for all items matching the current filters. They will be treated as found again.`,
          confirmLabel: "Clear status",
        }
      : null;

  const handleBulkConfirm = () => {
    if (!pendingBulk) return;
    const snapshot = pendingBulk;
    setPendingBulk(null);
    submitMissing(snapshot);
  };

  return (
    <GalleryPage title="Missing Media Review" count={items.length}>

      <Typography variant="body1" color="text.secondary" sx={{ mb: 2 }}>
        Review items whose files are currently missing before removing them from
        your library. Use bulk actions to confirm deletions or clear items once
        the files are restored.
      </Typography>

      {cleanupTask && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Cleanup task in progress (processed {cleanupTask.processed}/
          {cleanupTask.total}). This list will refresh automatically when it
          finishes.
        </Alert>
      )}

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      <Paper variant="outlined" sx={{ p: 2, mb: 3 }}>
        <Stack
          direction={{ xs: "column", md: "row" }}
          spacing={2}
          alignItems={{ xs: "flex-start", md: "center" }}
          justifyContent="space-between"
        >
          <Stack direction="row" spacing={2} alignItems="center">
            <Typography variant="h6">{totalMissing} files flagged</Typography>
            <FormControlLabel
              control={
                <Switch
                  checked={includeConfirmed}
                  onChange={(e) => setIncludeConfirmed(e.target.checked)}
                  color="primary"
                />
              }
              label="Include confirmed"
            />
          </Stack>
          <Stack
            direction={{ xs: "column", sm: "row" }}
            spacing={1.5}
            alignItems={{ xs: "stretch", sm: "center" }}
          >
            <TextField
              size="small"
              label="Path filter"
              value={pathFilter}
              onChange={(e) => setPathFilter(e.target.value)}
              placeholder="Enter a directory prefix"
              InputProps={{
                endAdornment: <FilterAltIcon color="action" fontSize="small" />,
              }}
              sx={{ minWidth: 260 }}
            />
            <Button
              variant="outlined"
              startIcon={<SelectAllIcon />}
              onClick={selectAllVisible}
              disabled={items.length === 0}
            >
              Select visible
            </Button>
            <Button
              variant="text"
              startIcon={<ClearAllIcon />}
              onClick={clearSelection}
              disabled={selectedCount === 0}
            >
              Clear selection
            </Button>
          </Stack>
        </Stack>
        {summary.length > 0 && (
          <>
            <Divider sx={{ my: 2 }} />
            <Typography variant="subtitle2" gutterBottom>
              Top folders with missing files
            </Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              {summary.map((entry) => (
                <Chip
                  key={entry.folder}
                  label={`${entry.folder} (${entry.count})`}
                  onClick={() => setPathFilter(entry.folder)}
                />
              ))}
            </Stack>
          </>
        )}
      </Paper>

      <Paper variant="outlined" sx={{ mb: 3 }}>
        <Stack
          direction={{ xs: "column", md: "row" }}
          spacing={1.5}
          alignItems={{ xs: "stretch", md: "center" }}
          justifyContent="space-between"
          sx={{ p: 2 }}
        >
          <Typography variant="subtitle1">
            Selected: {selectedCount} / {items.length}
          </Typography>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
            <Button
              variant="contained"
              color="error"
              startIcon={<DoneAllIcon />}
              onClick={() => requestBulk("confirm-selected")}
              disabled={selectedCount === 0}
            >
              Confirm selected
            </Button>
            <Button
              variant="outlined"
              startIcon={<UndoIcon />}
              onClick={handleResetSelected}
              disabled={selectedCount === 0}
            >
              Mark selected as found
            </Button>
            <Divider
              flexItem
              orientation="vertical"
              sx={{ display: { xs: "none", sm: "block" } }}
            />
            <Button
              variant="text"
              color="error"
              onClick={() => requestBulk("confirm-all")}
              disabled={totalMissing === 0}
            >
              Confirm all filtered
            </Button>
            <Button
              variant="text"
              onClick={() => requestBulk("reset-all")}
              disabled={totalMissing === 0}
            >
              Clear all filtered
            </Button>
          </Stack>
        </Stack>
        <Divider />
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell padding="checkbox">
                  <Checkbox inputProps={{ "aria-label": "Select all visible missing files" }} disabled={items.length === 0}
                    indeterminate={
                      selectedCount > 0 && selectedCount < items.length
                    }
                    checked={items.length > 0 && selectedCount === items.length}
                    onChange={() => {
                      if (selectedCount === items.length) {
                        clearSelection();
                      } else {
                        selectAllVisible();
                      }
                    }}
                  />
                </TableCell>
                <TableCell>Preview</TableCell>
                <TableCell>File</TableCell>
                <TableCell>Folder</TableCell>
                <TableCell align="right">Size</TableCell>
                <TableCell>Missing since</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {items.map((item) => {
                const selected = selectedIds.has(item.id);
                return (
                  <TableRow key={item.id} hover selected={selected}>
                    <TableCell padding="checkbox">
                      <Checkbox inputProps={{ "aria-label": `Select ${item.filename || `item ${item.id}`}` }}
                        checked={selected}
                        onChange={() => toggleSelection(item.id)}
                      />
                    </TableCell>
                    <TableCell>
                      <Box
                        sx={{
                          width: 56,
                          height: 56,
                          borderRadius: 1,
                          overflow: "hidden",
                          bgcolor: "action.hover",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                        }}
                      >
                        <Box
                          component="img"
                          src={buildThumbUrl(item)}
                          alt={item.filename}
                          loading="lazy"
                          sx={{
                            width: "100%",
                            height: "100%",
                            objectFit: "cover",
                            display: "block",
                          }}
                          onError={(e) => {
                            e.currentTarget.style.visibility = "hidden";
                          }}
                        />
                      </Box>
                    </TableCell>
                    <TableCell sx={{ fontFamily: "var(--font-mono)" }}>
                      {item.filename}
                    </TableCell>
                    <TableCell sx={{ fontFamily: "var(--font-mono)" }}>
                      {item.parent_directory}
                    </TableCell>
                    <TableCell align="right">
                      {formatBytes(item.size)}
                    </TableCell>
                    <TableCell>
                      {item.missing_since
                        ? `${formatDuration(item.missing_since)} (${parseUtcDate(item.missing_since).toLocaleString()})`
                        : "unknown"}
                    </TableCell>
                  </TableRow>
                );
              })}
              {!isLoading && items.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} align="center" sx={{ py: 4 }}>
                    No missing files match the current filters.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
        {isLoading && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
            <CircularProgress size={24} />
          </Box>
        )}
        {!isLoading && hasMore && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
            <Button
              onClick={loadMore}
              disabled={!hasMore}
            >
              Load more
            </Button>
          </Box>
        )}
      </Paper>

      <ConfirmDialog
        open={Boolean(pendingBulk)}
        title={bulkCopy?.title ?? ""}
        message={bulkCopy?.message ?? ""}
        confirmLabel={bulkCopy?.confirmLabel}
        confirmColor={pendingBulk?.kind === "reset-all" ? "primary" : "error"}
        onConfirm={handleBulkConfirm}
        onClose={() => setPendingBulk(null)}
      />


    </GalleryPage>
  );
};

export default MissingFilesPage;
