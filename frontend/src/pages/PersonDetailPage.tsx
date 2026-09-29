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
import { useState } from "react";
import Alert from "@mui/material/Alert";
import Snackbar from "@mui/material/Snackbar";
import { PersonContentTabs } from "../components/PersonContentTabs";
import { PersonHero } from "../components/PersonHero";
import ConfirmDialog from "../components/ConfirmDialog";
import { usePersonDetailPage } from "../hooks/usePersonDetailPage";
import PersonPicker from "../components/PersonPicker";
import { pickDirectory } from "../services/config";
import { exportPersonMedia } from "../services/personActions";

export default function PersonDetailPage() {
  const [exportDialogOpen, setExportDialogOpen] = useState(false);
  const [exportDestination, setExportDestination] = useState<string | null>(null);
  const [exportingMedia, setExportingMedia] = useState(false);
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

  const selectExportDestination = async () => {
    try {
      const selectedDirectory = await pickDirectory();
      if (!selectedDirectory) return;
      setExportDestination(selectedDirectory);
      setExportDialogOpen(true);
    } catch (error) {
      console.error("Failed to select export folder:", error);
      setSnackbar({
        open: true,
        message: "Failed to select an export folder",
        severity: "error",
      });
    }
  };

  const exportMedia = async (mode: "copy" | "move") => {
    if (!exportDestination) return;
    setExportingMedia(true);
    try {
      const result = await exportPersonMedia(
        person?.id ?? 0,
        exportDestination,
        mode,
      );
      const action = mode === "copy" ? "Copied" : "Moved";
      const skipped = result.skipped.length;
      setSnackbar({
        open: true,
        message: `${action} ${result.completed} media file${result.completed === 1 ? "" : "s"}${
          skipped ? `; skipped ${skipped}` : ""
        }.`,
        severity: skipped && result.completed === 0 ? "error" : "success",
      });
      setExportDialogOpen(false);
      setExportDestination(null);
    } catch (error) {
      console.error(`Failed to ${mode} person media:`, error);
      setSnackbar({
        open: true,
        message:
          error instanceof Error ? error.message : `Failed to ${mode} media`,
        severity: "error",
      });
    } finally {
      setExportingMedia(false);
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
        exportingMedia={exportingMedia}
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
        message="Are you sure you want to delete this person?"
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
