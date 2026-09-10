import config from "../config";
import { useHotkey } from "../hotkeys/useHotkey";
import { SelectionHotkeyDialogs } from "../hotkeys/SelectionHotkeyDialogs";
import { useUndo, useUndoRefresh, useMutationRefresh } from "../context/UndoContext";
import { runOptimistic } from "../stores/mutationBus";
import ListState from "../components/ListState";
import { refreshCachedList } from "../stores/useListStore";
import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  Chip,
  Container,
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
import {
  assignFace,
  detachFace,
  createPersonFromFaces,
  deleteFace,
  deleteAllOrphanFaces,
} from "../services/faceActions";
import PersonPicker from "../components/PersonPicker";
import { Person } from "../types";
import { FaceGrid } from "../components/FaceGrid"; // Import our DUMB grid component
import ConfirmDialog from "../components/ConfirmDialog";
import OrphanFaceSuggestions from "../components/OrphanFaceSuggestions";
import { StickySelectionToolbar } from "../components/BulkResolveToolbar";

export default function OrphanFacesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const view = searchParams.get("view") === "suggestions" ? "suggestions" : "all";
  const rawMin = searchParams.get("min");
  const parsedMin = rawMin?.trim() ? Number(rawMin) : NaN;
  const minScore = Number.isFinite(parsedMin)
    ? Math.round(Math.min(0.8, Math.max(0.4, parsedMin)) * 100) / 100
    : 0.5;

  return (
    <>
      <Container maxWidth="xl" sx={{ pt: 4 }}>
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
          <ToggleButton value="suggestions">Suggestions</ToggleButton>
        </ToggleButtonGroup>
      </Container>
      {view === "suggestions" ? (
        <OrphanFaceSuggestions
          minScore={minScore}
          onMinScoreChange={(value) => {
            const next = new URLSearchParams(searchParams);
            next.set("min", value.toFixed(2));
            setSearchParams(next);
          }}
        />
      ) : <AllOrphanFaces />}
    </>
  );
}

function AllOrphanFaces() {
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
  } = useListStore((state) => state.lists[listKey] || defaultListState);
  const fetchInitial = useListStore((state) => state.fetchInitial);
  const loadMore = useListStore((state) => state.loadMore);
  const clearList = useListStore((state) => state.clearList);
  const [optimisticFaceIds, setOptimisticFaceIds] = useState<number[]>([]);
  const orphans = storedOrphans.filter((face) => !optimisticFaceIds.includes(face.id));

  // All UI state is now managed directly by the page
  const [isProcessing, setIsProcessing] = useState(false);
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

  const showMessage = (
    message: string,
    severity: "success" | "error" = "success"
  ) => {
    setSnackbar({ open: true, message, severity });
  };

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

  const optimisticallyRemoveFaces = <T,>(faceIds: number[], request: () => Promise<T>) => runOptimistic({
    apply: () => { setOptimisticFaceIds((previous) => [...new Set([...previous, ...faceIds])]); },
    request,
    rollback: () => { setOptimisticFaceIds((previous) => previous.filter((id) => !faceIds.includes(id))); },
  });

  // --- Action Handlers ---
  const handleDeleteAll = async () => {
    setIsProcessing(true);
    try {
      const { deleted } = await deleteAllOrphanFaces();
      setSelectedFaceIds([]);
      showMessage(`Deleted ${deleted.toLocaleString()} unassigned face${deleted === 1 ? "" : "s"}.`);
    } catch (reason) {
      showMessage(reason instanceof Error ? reason.message : "Failed to delete all unassigned faces.", "error");
    } finally {
      setIsProcessing(false);
      setConfirmDeleteAllOpen(false);
    }
  };

  const handleBulkDelete = async () => {
    const faceIds = [...selectedFaceIds];
    setIsProcessing(true);
    try {
      await optimisticallyRemoveFaces(faceIds, () => deleteFace(faceIds));
      setSelectedFaceIds([]);
      showMessage(
        `Deleted ${faceIds.length} face${faceIds.length === 1 ? "" : "s"}.`
      );
    } catch (err) {
      console.error("Failed to delete faces:", err);
      showMessage("Failed to delete faces.", "error");
    } finally {
      setOptimisticFaceIds((previous) => previous.filter((id) => !faceIds.includes(id)));
      setIsProcessing(false);
      setConfirmDeleteOpen(false);
    }
  };

  const handleBulkCreate = async () => {
    if (!newPersonName.trim()) return;
    const faceIds = [...selectedFaceIds];
    setIsProcessing(true);
    try {
      const newPerson = await optimisticallyRemoveFaces(faceIds, () => createPersonFromFaces(faceIds, newPersonName));
      if (!newPerson?.id) {
        throw new Error("Failed to get ID for newly created person.");
      }
      setSelectedFaceIds([]);
      setCreateDialogOpen(false);
      navigate(`/person/${newPerson.id}`, { state: { forceRefresh: true } });
    } catch (err) {
      console.error("Failed to create and navigate to new person:", err);
      showMessage("Failed to create new person.", "error");
    } finally {
      setOptimisticFaceIds((previous) => previous.filter((id) => !faceIds.includes(id)));
      setIsProcessing(false);
    }
  };

  const openAssignDialog = () => {
    setAssignDialogOpen(true);
  };

  const handleConfirmAssign = async (person: Person | null) => {
    if (!person) return;
    const faceIds = [...selectedFaceIds];
    setIsProcessing(true);
    try {
      await optimisticallyRemoveFaces(faceIds, () => assignFace(faceIds, person.id));
      setSnackbar((previous) => ({ ...previous, open: false }));
      push({
        label: `Assigned ${faceIds.length} face${faceIds.length === 1 ? "" : "s"} to ${person.name || `Person ${person.id}`}`,
        undo: async () => {
          await detachFace(faceIds);
          await refreshVisible();
        },
      });
      setSelectedFaceIds([]);
      setAssignDialogOpen(false);
    } catch (err) {
      console.error("Failed to assign faces:", err);
      showMessage("Failed to assign faces.", "error");
    } finally {
      setOptimisticFaceIds((previous) => previous.filter((id) => !faceIds.includes(id)));
      setIsProcessing(false);
    }
  };

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

  useHotkey({ key: "a" }, openAssignDialog, { scope: "page", enabled: selectedFaceIds.length > 0 && !isProcessing && !config.PRESENTATION_MODE, description: "Assign selected faces…" });
  useHotkey({ key: "Escape" }, () => setSelectedFaceIds([]), { scope: "page", enabled: selectedFaceIds.length > 0, description: "Clear face selection" });


  return (
    <Container id="unassigned-faces" maxWidth="xl" sx={{ pt: 4, pb: 7 }}>
      <Box sx={{ display: "flex", alignItems: "center", flexWrap: "wrap", mb: 2, gap: 2 }}>
        <Typography variant="h4" sx={{ flexGrow: 1 }}>
          Unassigned Faces
        </Typography>
      </Box>

      {countError && <Alert severity="error" sx={{ mb: 2 }}>{countError}</Alert>}

      <StickySelectionToolbar sx={{ p: 1, mb: 2 }}>
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
      </StickySelectionToolbar>

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
        />
      )}

      {/* The loader and sentinel are at the page level */}
      {isLoading && orphans.length > 0 && (
        <Box textAlign="center" py={4}>
          <CircularProgress />
        </Box>
      )}
      {hasMore && <Box ref={loaderRef} sx={{ height: "50px" }} />}

      <SelectionHotkeyDialogs includeDelete enabled={!isProcessing}
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
        <DialogContent>
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
    </Container>
  );
}
