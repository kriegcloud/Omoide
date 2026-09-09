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
import Alert from "@mui/material/Alert";
import Snackbar from "@mui/material/Snackbar";
import { PersonContentTabs } from "../components/PersonContentTabs";
import { PersonHero } from "../components/PersonHero";
import ConfirmDialog from "../components/ConfirmDialog";
import { usePersonDetailPage } from "../hooks/usePersonDetailPage";
import PersonPicker from "../components/PersonPicker";

export default function PersonDetailPage() {
  const {
    person,
    loading,
    saving,
    mergeOpen,
    setMergeOpen,
    mergeTarget,
    setMergeTarget,
    similarPersons,
    suggestedFaces,
    relationshipGraph,
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

      <Dialog open={mergeTarget !== null} onClose={() => setMergeTarget(null)}>
        <DialogTitle>Confirm Merge</DialogTitle>
        <DialogContent>
          <Typography>
            {/* Display both names for clarity */}
            Are you sure you want to merge "{person.name}" into "
            {mergeTarget?.name}"?
          </Typography>
          <Typography color="text.secondary" sx={{ mt: 1 }}>
            This action cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setMergeTarget(null)}>Cancel</Button>
          <Button
            onClick={handleConfirmMerge}
            color="primary"
            variant="contained"
          >
            Confirm Merge
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
