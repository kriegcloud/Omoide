import { FaceGroups, IgnoredFaces } from "../components/FaceReviewViews";
import { appliedFaceIds, startFaceReview, startLegacyFaceReview } from "../services/faceReview";
import GalleryPage from "../components/GalleryPage";
import { useOptimisticCachedList } from "../stores/useListStore";
import config from "../config";
import { useHotkey } from "../hotkeys/useHotkey";
import { SelectionHotkeyDialogs } from "../hotkeys/SelectionHotkeyDialogs";
import { useUndo, useUndoRefresh, useMutationRefresh } from "../context/UndoContext";
import ListState from "../components/ListState";
import { refreshCachedList } from "../stores/useListStore";
import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  Chip,
  Box,
  Typography,
  CircularProgress,
  Dialog,
  DialogTitle,
  DialogContent,
  TextField,
  DialogActions,
  Button,
  Stack,
  Alert,
  Snackbar,
  ToggleButton,
  ToggleButtonGroup,
} from "@mui/material";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useInView } from "react-intersection-observer";
import { useListStore, defaultListState, useListInvalidation } from "../stores/useListStore";
import { getOrphanFaces, getOrphanFaceCount } from "../services/face";
import PersonPicker, { PERSON_PICKER_DIALOG_CONTENT_SX } from "../components/PersonPicker";
import { FaceRead, Person } from "../types";
import { FaceGrid } from "../components/FaceGrid"; // Import our DUMB grid component
import ConfirmDialog from "../components/ConfirmDialog";
import OrphanFaceSuggestions from "../components/OrphanFaceSuggestions";
import { StickySelectionToolbar } from "../components/BulkResolveToolbar";

export default function OrphanFacesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedView = searchParams.get("view");
  const view = ["groups", "suggestions", "ignored"].includes(requestedView ?? "") ? requestedView : "all";
  const rawMin = searchParams.get("min");
  const parsedMin = rawMin?.trim() ? Number(rawMin) : NaN;
  const minScore = Number.isFinite(parsedMin)
    ? Math.round(Math.min(1, Math.max(0.48, parsedMin)) * 100) / 100
    : 0.48;

  const tabs = (
        <ToggleButtonGroup
          exclusive
          value={view}
          aria-label="Unassigned faces view"
          onChange={(_, nextView: string | null) => {
            if (!nextView) return;
            const next = new URLSearchParams(searchParams);
            next.set("view", nextView);
            setSearchParams(next);
          }}
        >
          <ToggleButton value="all">All</ToggleButton>
          <ToggleButton value="groups">Groups</ToggleButton>
          <ToggleButton value="suggestions">Suggestions</ToggleButton>
          <ToggleButton value="ignored">Ignored</ToggleButton>
        </ToggleButtonGroup>
  );
  return (view === "groups" ? <FaceGroups tabs={tabs} /> : view === "ignored" ? <IgnoredFaces tabs={tabs} /> : view === "suggestions" ? (
        <OrphanFaceSuggestions
          tabs={tabs}
          minScore={minScore}
          onMinScoreChange={(value) => {
            const next = new URLSearchParams(searchParams);
            next.set("min", value.toFixed(2));
            setSearchParams(next);
          }}
        />
      ) : <AllOrphanFaces tabs={tabs} />
  );
}

export function AllOrphanFaces({ tabs }: { tabs: React.ReactNode }) {
  const navigate = useNavigate();
  const listKey = "orphan-faces";
  useListInvalidation(listKey);
  const { push, refreshVisible } = useUndo();
  useUndoRefresh(`cache:${listKey}`, () => refreshCachedList(listKey));

  // --- State Management ---
  const {
    items: storedOrphans,
    hasMore,
    isLoading,
    error,
  } = useOptimisticCachedList<FaceRead>(listKey, "face") || defaultListState;
  const fetchInitial = useListStore((state) => state.fetchInitial);
  const loadMore = useListStore((state) => state.loadMore);
  const clearList = useListStore((state) => state.clearList);
  const orphans = storedOrphans;

  // All UI state is now managed directly by the page
  const isProcessing = false;
  const [selectedFaceIds, setSelectedFaceIds] = useState<number[]>([]);
  const [orphanCount, setOrphanCount] = useState<number | null>(null);
  const [countError, setCountError] = useState<string | null>(null);
  const countRequest = useRef(0);

  // State for dialogs
  const [assignDialogOpen, setAssignDialogOpen] = useState(false);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [confirmDeleteAllOpen, setConfirmDeleteAllOpen] = useState(false);
  const [newPersonName, setNewPersonName] = useState("");
  const [snackbar, setSnackbar] = useState<{
    open: boolean;
    message: string;
    severity: "success" | "error";
  }>({ open: false, message: "", severity: "success" });

  // --- Infinite Scroll ---
  const { ref: loaderRef, inView } = useInView({
    threshold: 0.5,
    skip: isLoading || !hasMore || isProcessing || !!error,
    rootMargin: "0px 0px 200px 0px",
  });

  useEffect(() => {
    clearList(listKey);
    fetchInitial(listKey, () => getOrphanFaces(null));
  }, [clearList, fetchInitial, listKey]);

  useEffect(() => {
    if (inView && hasMore && !isLoading && !error && !isProcessing) {
      loadMore(listKey, (cursor) => getOrphanFaces(cursor));
    }
  }, [inView, hasMore, isLoading, error, loadMore, listKey, isProcessing]);

  const refreshCount = useCallback(async () => {
    const request = ++countRequest.current;
    try {
      const count = await getOrphanFaceCount();
      if (countRequest.current !== request) return;
      setOrphanCount(count);
      setCountError(null);
    } catch (reason) {
      if (countRequest.current !== request) return;
      setOrphanCount(null);
      setCountError(reason instanceof Error ? reason.message : "Failed to load the total face count");
    }
  }, []);
  useEffect(() => {
    if (!isProcessing) void refreshCount();
    return () => { countRequest.current += 1; };
  }, [storedOrphans.length, isProcessing, refreshCount]);
  useUndoRefresh("orphan-count", refreshCount);
  useMutationRefresh(["face:deleted", "face:assigned", "face:detached", "person:created", "media:deleted", "list:invalidate"], async () => {
    await Promise.all([refreshCount(), refreshCachedList(listKey)]);
  });

  const handleDeleteAll = async () => {
    startLegacyFaceReview("Delete all unassigned faces", orphans.map(face => face.id), "orphans", {}, { method: "DELETE" });
    setSelectedFaceIds([]);
    setConfirmDeleteAllOpen(false);
  };
  const handleBulkDelete = async () => {
    const params = new URLSearchParams();
    selectedFaceIds.forEach(id => params.append("face_ids", String(id)));
    startLegacyFaceReview("Delete faces", selectedFaceIds, `?${params}`, {}, { method: "DELETE" });
    setSelectedFaceIds([]);
    setConfirmDeleteOpen(false);
  };
  const handleBulkCreate = async () => {
    if (!newPersonName.trim()) return;
    startLegacyFaceReview<Person | { person: Person }>("Create person", selectedFaceIds, "create_person", { face_ids: selectedFaceIds, name: newPersonName.trim() }, {
      onResult: result => { const person = "person" in result ? result.person : result; if (person.id) navigate(`/person/${person.id}`, { state: { forceRefresh: true } }); },
    });
    setSelectedFaceIds([]);
    setCreateDialogOpen(false);
  };
  const openAssignDialog = () => { setAssignDialogOpen(true); };
  const handleConfirmAssign = async (person: Person | null) => {
    if (!person) return;
    const faceIds = [...selectedFaceIds];
    const operation = startLegacyFaceReview("Assign faces", faceIds, "assign", { face_ids: faceIds, person_id: person.id, source: "manual" });
    setSelectedFaceIds([]);
    setAssignDialogOpen(false);
    const record = await operation.done;
    if (record.receipt?.applied.length) push({ label: `Assigned faces to ${person.name || `Person ${person.id}`}`, undo: async () => {
      const ids = appliedFaceIds(record.receipt!);
      await startLegacyFaceReview("Undo assignment", ids, "detach", { face_ids: ids }).done;
      await refreshVisible();
    } });
  };

  const ignore = (ids: number[]) => {
    if (config.PRESENTATION_MODE || !ids.length) return;
    const rows = orphans.filter(face => ids.includes(face.id));
    const operation = startFaceReview("ignore", ids, {}, rows);
    setSelectedFaceIds([]);
    void operation.done.then(record => {
      if (record.receipt?.applied.length) push({ label: "Ignored faces", undo: async () => {
        await startFaceReview("unignore", appliedFaceIds(record.receipt!), {}, rows).done;
        await refreshVisible();
      } });
    });
  };
  useHotkey({ key: "i" }, () => ignore(selectedFaceIds), { scope: "page", enabled: selectedFaceIds.length > 0 && !config.PRESENTATION_MODE, description: "Ignore selected faces" });

  const handleRetry = () => {
    clearList(listKey);
    fetchInitial(listKey, () => getOrphanFaces(null));
  };

  const handleSelectAll = () => {
    if (selectedFaceIds.length < orphans.length) {
      setSelectedFaceIds(orphans.map((f) => f.id));
    } else {
      setSelectedFaceIds([]);
    }
  };

  useHotkey({ key: "n" }, () => setCreateDialogOpen(true), { scope: "page", enabled: selectedFaceIds.length > 0 && !config.PRESENTATION_MODE, description: "Create person from selected faces…" });
  useHotkey({ key: "Delete" }, () => setConfirmDeleteOpen(true), { scope: "page", enabled: selectedFaceIds.length > 0 && !config.PRESENTATION_MODE, description: "Delete selected faces…" });
  useHotkey({ key: "a" }, openAssignDialog, { scope: "page", enabled: selectedFaceIds.length > 0 && !isProcessing && !config.PRESENTATION_MODE, description: "Assign selected faces…" });
  useHotkey({ key: "Escape" }, () => setSelectedFaceIds([]), { scope: "page", enabled: selectedFaceIds.length > 0, description: "Clear face selection" });

  return (
    <GalleryPage id="unassigned-faces" title="Unassigned Faces" tabs={tabs} count={orphanCount ?? storedOrphans.length} countLabel="faces" selection={<StickySelectionToolbar sx={{ p: 1, mb: 2 }} selectedCount={selectedFaceIds.length} onClearSelection={() => setSelectedFaceIds([])}>
        <Stack direction="row" spacing={1} alignItems="center" useFlexGap flexWrap="wrap">
          <Typography sx={{ ml: 1 }} variant="subtitle1">
            {selectedFaceIds.length} selected · {orphans.length} loaded
          </Typography>
          {hasMore && <Chip size="small" label="Load more to select the rest" />}
          <Box sx={{ flexGrow: 1 }} />
          <Button
            size="small"
            onClick={handleSelectAll}
            disabled={orphans.length === 0}
          >
            {selectedFaceIds.length < orphans.length ? "Select All" : "Select None"}
          </Button>
          {selectedFaceIds.length > 0 && (
            <>
              <Button size="small" disabled={config.PRESENTATION_MODE} onClick={() => ignore(selectedFaceIds)}>Ignore</Button>
              <Button
                variant="contained"
                size="small"
                disabled={isProcessing}
                onClick={openAssignDialog}
              >
                Assign...
              </Button>
              <Button
                variant="contained"
                size="small"
                disabled={isProcessing}
                onClick={() => setCreateDialogOpen(true)}
              >
                Create...
              </Button>
              <Button
                variant="outlined"
                color="error"
                size="small"
                disabled={isProcessing}
                onClick={() => setConfirmDeleteOpen(true)}
              >
                Delete
              </Button>
            </>
          )}
          <Button
            size="small"
            variant="outlined"
            color="error"
            disabled={isProcessing || orphanCount === null || orphanCount === 0}
            onClick={() => setConfirmDeleteAllOpen(true)}
          >
            Delete all ({orphanCount === null ? "…" : orphanCount.toLocaleString()})
          </Button>
          {isProcessing && <CircularProgress size={20} />}
        </Stack>
      </StickySelectionToolbar>}>

      {countError && <Alert severity="error" sx={{ mb: 2 }}>{countError}</Alert>}

      <ListState
        loading={isLoading && orphans.length === 0}
        error={error ? `Failed to load unassigned faces: ${error}` : null}
        empty={orphans.length === 0 && !isLoading}
        emptyMessage="No unassigned faces found."
        onRetry={handleRetry}
        action={<Button onClick={() => navigate("/people")}>Browse people</Button>}
      />
      {orphans.length > 0 && (
        <FaceGrid
          faces={orphans}
          selectedFaceIds={selectedFaceIds}
          onSelectionChange={setSelectedFaceIds}
          renderFooter={face => <Button data-tile-control data-no-marquee size="small" disabled={config.PRESENTATION_MODE} onClick={() => ignore([face.id])}>Ignore</Button>}
        />
      )}

      {/* The loader and sentinel are at the page level */}
      {isLoading && orphans.length > 0 && (
        <Box textAlign="center" py={4}>
          <CircularProgress />
        </Box>
      )}
      {hasMore && <Box ref={loaderRef} sx={{ height: "50px" }} />}

      <SelectionHotkeyDialogs enabled={!isProcessing}
        mediaIds={[...new Set(orphans.filter(face => selectedFaceIds.includes(face.id)).map(face => face.media_id))]}
        onProcessed={() => setSelectedFaceIds([])} />

      {/* --- Dialogs --- */}
      {/* Assign Dialog */}
      <Dialog
        open={assignDialogOpen}
        onClose={isProcessing ? undefined : () => setAssignDialogOpen(false)}
        fullWidth
      >
        <DialogTitle>Assign {selectedFaceIds.length} faces to...</DialogTitle>
        <DialogContent sx={PERSON_PICKER_DIALOG_CONTENT_SX}>
          {assignDialogOpen && (
            <PersonPicker
              autoFocus
              disabled={isProcessing}
              onSelect={(person) => void handleConfirmAssign(person)}
            />
          )}
        </DialogContent>
        <DialogActions>
          <Button disabled={isProcessing} onClick={() => setAssignDialogOpen(false)}>
            Cancel
          </Button>
        </DialogActions>
      </Dialog>
      {/* Create Dialog */}
      <Dialog
        open={createDialogOpen}
        onClose={() => setCreateDialogOpen(false)}
      >
        <DialogTitle>Create New Person</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            label="Person Name"
            type="text"
            fullWidth
            variant="standard"
            value={newPersonName}
            onChange={(e) => setNewPersonName(e.target.value)}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreateDialogOpen(false)}>Cancel</Button>
          <Button onClick={handleBulkCreate} disabled={isProcessing}>
            Create
          </Button>
        </DialogActions>
      </Dialog>

      <ConfirmDialog
        open={confirmDeleteAllOpen}
        title="Delete All Unassigned Faces"
        message={`Permanently delete all ${(orphanCount ?? 0).toLocaleString()} unassigned faces, including faces that are not loaded on this page? Original photos and videos are kept. This cannot be undone.`}
        confirmLabel="Delete all"
        loading={isProcessing}
        onConfirm={handleDeleteAll}
        onClose={() => setConfirmDeleteAllOpen(false)}
      />

      <ConfirmDialog
        open={confirmDeleteOpen}
        title="Delete Faces"
        message={`Are you sure you want to delete ${selectedFaceIds.length} face${selectedFaceIds.length === 1 ? "" : "s"}? This cannot be undone.`}
        confirmLabel="Delete"
        loading={isProcessing}
        onConfirm={handleBulkDelete}
        onClose={() => setConfirmDeleteOpen(false)}
      />

      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert
          severity={snackbar.severity}
          onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
          sx={{ width: "100%" }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </GalleryPage>
  );
}
