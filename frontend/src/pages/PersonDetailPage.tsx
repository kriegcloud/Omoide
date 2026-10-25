import {
  Box,
  Button,
  CircularProgress,
  Container,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Typography,
} from "@mui/material";
import { useEffect, useRef, useState } from "react";
import Alert from "@mui/material/Alert";
import Snackbar from "@mui/material/Snackbar";
import { PersonContentTabs } from "../components/PersonContentTabs";
import { PersonHero } from "../components/PersonHero";
import ConfirmDialog from "../components/ConfirmDialog";
import { usePersonDetailPage } from "../hooks/usePersonDetailPage";
import PersonPicker from "../components/PersonPicker";
import { pickDirectory } from "../services/config";
import { exportPersonMedia } from "../services/personActions";
import { getTask } from "../services/task";
import { Task } from "../types";

const EXPORT_POLL_INTERVAL_MS = 1500;

export default function PersonDetailPage() {
  const [exportDialogOpen, setExportDialogOpen] = useState(false);
  const [exportDestination, setExportDestination] = useState<string | null>(null);
  const [exportingMedia, setExportingMedia] = useState(false);
  const [exportTask, setExportTask] = useState<Task | null>(null);
  const {
    person,
    loading,
    loadError,
    retryDetail,
    saving,
    mergeOpen,
    setMergeOpen,
    mergeTarget,
    setMergeTarget,
    similarPersons,
    suggestedFaces,
    relationshipGraph,
    canExportMedia,
    relationshipDepth,
    isLoadingRelationships,
    hasLoadedRelationships,
    filterPeople,
    setFilterPeople,
    filterTags,
    setFilterTags,
    mediaListKey,
    detectedFacesList,
    hasMoreFaces,
    loadingMoreFaces,
    snackbar,
    setSnackbar,
    confirmDelete,
    deleteTarget,
    isDeletingPerson,
    setConfirmDelete,
    loadSimilar,
    loadRelationshipGraph,
    refreshSuggestedFaces,
    loadMoreDetectedFaces,
    mergeSelectedSimilar,
    autoMergeSimilar,
    isMergingSimilar,
    handleAssignWrapper,
    handleDeleteWrapper,
    handleDetachWrapper,
    handleDetachMediaWrapper,
    handleCreateWrapper,
    handleAutoSelectProfileFace,
    handleHideToggle,
    handleProfileAssignmentWrapper,
    handlePersonUpdate,
    handleDeletePerson,
    handleTagAddedToPerson,
    onSave,
    handleGenderChange,
    handleConfirmMerge,
    isAutoSelectingProfile,
    isLoadingSuggestedFaces,
    suggestedFacesLimit,
    setSuggestedFacesLimit,
    facesSortBy,
    handleFacesSortChange,
    fetchFacesForMedia,
  } = usePersonDetailPage();

  const exportGeneration = useRef(0);
  const exportPersonId = useRef(person?.id); exportPersonId.current = person?.id;
  useEffect(() => {
    exportGeneration.current += 1;
    setExportTask(null); setExportDialogOpen(false); setExportDestination(null); setExportingMedia(false);
    return () => { exportGeneration.current += 1; };
  }, [person?.id]);

  const selectExportDestination = async () => {
    const version = exportGeneration.current;
    try {
      const selectedDirectory = await pickDirectory();
      if (!selectedDirectory || version !== exportGeneration.current) return;
      setExportDestination(selectedDirectory);
      setExportDialogOpen(true);
    } catch (error) {
      if (version !== exportGeneration.current) return;
      console.error("Failed to select export folder:", error);
      setSnackbar({
        open: true,
        message: "Failed to select an export folder",
        severity: "error",
      });
    }
  };

  // The export runs as a background task; poll it to report the outcome.
  useEffect(() => {
    if (!exportTask || !["pending", "running"].includes(exportTask.status)) {
      return;
    }
    let cancelled = false;
    const timeoutId = window.setTimeout(async () => {
      try {
        const updated = await getTask(exportTask.id);
        if (cancelled) return;
        if (["pending", "running"].includes(updated.status)) {
          setExportTask(updated);
          return;
        }
        setExportTask(null);
        if (updated.status !== "completed") {
          setSnackbar({
            open: true,
            message: `Media export ${updated.status}.`,
            severity: updated.status === "failed" ? "error" : "info",
          });
          return;
        }
        const result = updated.result;
        const exported = result?.exported, skipped = result?.skipped;
        const count = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
        if (!count(exported) || !count(skipped)) {
          setSnackbar({open: true, message: "Media export completed; outcome counts are unavailable.", severity: "warning"});
          return;
        }
        setSnackbar({
          open: true,
          message: `Exported ${exported} media file${exported === 1 ? "" : "s"}${skipped ? `; skipped ${skipped}` : ""}.`,
          severity: exported === 0 && skipped > 0 ? "error" : skipped > 0 ? "warning" : "success",
        });
      } catch (error) {
        if (cancelled) return;
        console.error("Failed to fetch export task status:", error);
        setExportTask(null);
      }
    }, EXPORT_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [exportTask, setSnackbar]);

  const exportMedia = async (mode: "copy" | "move") => {
    if (!exportDestination || !person) return;
    const target = person.id, version = exportGeneration.current;
    const current = () => exportPersonId.current === target && exportGeneration.current === version;
    setExportingMedia(true);
    try {
      const task = await exportPersonMedia(
        target,
        exportDestination,
        mode,
      );
      if (!current()) return;
      setExportTask(task);
      setSnackbar({
        open: true,
        message: `${mode === "copy" ? "Copying" : "Moving"} media in the background; progress is shown under Tasks.`,
        severity: "info",
      });
      setExportDialogOpen(false);
      setExportDestination(null);
    } catch (error) {
      if (!current()) return;
      console.error(`Failed to ${mode} person media:`, error);
      setSnackbar({
        open: true,
        message:
          error instanceof Error ? error.message : `Failed to ${mode} media`,
        severity: "error",
      });
    } finally {
      if (current()) setExportingMedia(false);
    }
  };

  if (!loading && (loadError || !person)) {
    return (
      <Container sx={{ py: 4 }}>
        <Alert severity="error" action={<Button color="inherit" onClick={() => void retryDetail()}>Retry</Button>}>
          {loadError || "Person not found"}
        </Alert>
      </Container>
    );
  }

  if (loading || !person) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "100vh",
        }}
      >
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Container maxWidth="xl" sx={{ pt: 2, pb: 6 }}>
      <PersonHero
        person={person}
        onSave={onSave}
        onGenderChange={handleGenderChange}
        saving={saving}
        onMerge={() => setMergeOpen(true)}
        onDelete={() => setConfirmDelete(true)}
        onRefreshSimilar={loadSimilar}
        onAutoSelectProfile={handleAutoSelectProfileFace}
        onHideToggle={handleHideToggle}
        autoSelectingProfile={isAutoSelectingProfile}
        onExportMedia={selectExportDestination}
        canExportMedia={canExportMedia}
        exportingMedia={exportingMedia || exportTask !== null}
      />

      <PersonContentTabs
        person={person}
        onTagUpdate={handlePersonUpdate}
        onTagAdded={handleTagAddedToPerson}
        detectedFacesList={detectedFacesList}
        hasMoreFaces={hasMoreFaces}
        loadingMoreFaces={loadingMoreFaces}
        loadMoreDetectedFaces={loadMoreDetectedFaces}
        handleProfileAssignmentWrapper={handleProfileAssignmentWrapper}
        handleAssignWrapper={handleAssignWrapper}
        handleDeleteWrapper={handleDeleteWrapper}
        handleDetachWrapper={handleDetachWrapper}
        onLoadSimilar={loadSimilar}
        suggestedFaces={suggestedFaces}
        similarPersons={similarPersons}
        onRefreshSuggestions={refreshSuggestedFaces}
        handleCreateWrapper={handleCreateWrapper}
        onMergeSelectedSimilar={mergeSelectedSimilar}
        onAutoMergeSimilar={autoMergeSimilar}
        isMergingSimilar={isMergingSimilar}
        isLoadingSuggestedFaces={isLoadingSuggestedFaces}
        suggestedFacesLimit={suggestedFacesLimit}
        onSuggestedFacesLimitChange={setSuggestedFacesLimit}
        filterPeople={filterPeople}
        onFilterPeopleChange={(people) => setFilterPeople(people)}
        filterTags={filterTags}
        onFilterTagsChange={(tags) => setFilterTags(tags)}
        mediaListKey={mediaListKey}
        relationshipGraph={relationshipGraph}
        relationshipDepth={relationshipDepth}
        isLoadingRelationships={isLoadingRelationships}
        hasLoadedRelationships={hasLoadedRelationships}
        onLoadRelationships={(depth) => loadRelationshipGraph(depth)}
        onDetachMedia={handleDetachMediaWrapper}
        facesSortBy={facesSortBy}
        onFacesSortChange={handleFacesSortChange}
        onFetchFacesForMedia={fetchFacesForMedia}
      />

      <Snackbar
        open={snackbar.open}
        autoHideDuration={3000}
        onClose={() => setSnackbar({ ...snackbar, open: false })}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert
          severity={snackbar.severity}
          sx={{ width: "100%" }}
          onClose={() => setSnackbar({ ...snackbar, open: false })}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>

      {/* Confirm Delete Dialog */}
      <ConfirmDialog
        open={confirmDelete}
        title="Confirm Deletion"
        message={`Delete "${deleteTarget?.name ?? "this person"}"?`}
        loading={isDeletingPerson}
        confirmLabel="Delete"
        onConfirm={handleDeletePerson}
        onClose={() => setConfirmDelete(false)}
      />

      <ConfirmDialog open={mergeTarget !== null} title="Confirm Merge"
        message={`Are you sure you want to merge "${person.name}" into "${mergeTarget?.name}"? This action cannot be undone.`}
        confirmLabel="Confirm Merge" confirmColor="primary" loading={saving}
        onConfirm={handleConfirmMerge} onClose={() => setMergeTarget(null)} />

      <Dialog
        open={exportDialogOpen}
        onClose={exportingMedia ? undefined : () => setExportDialogOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Export media to folder</DialogTitle>
        <DialogContent>
          <Typography>
            {person.appearance_count} media file
            {person.appearance_count === 1 ? "" : "s"} containing this
            person will be exported to:
          </Typography>
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ mt: 1, overflowWrap: "anywhere" }}
          >
            {exportDestination}
          </Typography>
          <Typography color="text.secondary" sx={{ mt: 2 }}>
            Copy keeps the originals in their current folders. Move relocates
            them and updates their paths in your library. Existing files are
            never overwritten.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button
            onClick={() => setExportDialogOpen(false)}
            disabled={exportingMedia}
          >
            Cancel
          </Button>
          <Button
            variant="outlined"
            onClick={() => exportMedia("copy")}
            disabled={exportingMedia}
          >
            Copy
          </Button>
          <Button
            color="warning"
            variant="contained"
            onClick={() => exportMedia("move")}
            disabled={exportingMedia}
            startIcon={exportingMedia ? <CircularProgress size={16} /> : undefined}
          >
            Move
          </Button>
        </DialogActions>
      </Dialog>

      {/* Merge Dialog */}
      <Dialog open={mergeOpen} onClose={() => setMergeOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>Merge "{person.name}" into...</DialogTitle>
        <DialogContent>
          {mergeOpen && (
            <PersonPicker
              autoFocus
              label="Search by name..."
              excludeIds={[person.id]}
              onSelect={(candidate) => setMergeTarget({
                id: candidate.id,
                name: candidate.name ?? "Unknown",
              })}
            />
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setMergeOpen(false)}>Cancel</Button>
        </DialogActions>
      </Dialog>
    </Container>
  );
}
