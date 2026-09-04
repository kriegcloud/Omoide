import { useCallback, useEffect, useMemo, useState } from "react";
import RefreshIcon from "@mui/icons-material/Refresh";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Container,
  Snackbar,
  Stack,
  Typography,
} from "@mui/material";
import Grid from "@mui/material/Grid";
import { useInView } from "react-intersection-observer";
import ConfirmDialog from "../components/ConfirmDialog";
import MergePeopleDialog from "../components/MergePeopleDialog";
import PersonCard from "../components/PersonCard";
import config from "../config";
import { usePeopleSelection } from "../hooks/usePeopleSelection";
import { getPeople } from "../services/person";
import {
  deletePersonsBulk,
  mergeMultiplePersons,
} from "../services/personActions";
import {
  defaultListState,
  ListState,
  useListStore,
} from "../stores/useListStore";
import { useTaskCompletionVersion } from "../TaskEventsContext";
import { Person, PersonReadSimple } from "../types";

const PEOPLE_LIST_KEY = "people-grid";

export default function PeoplePage() {
  const { ref: loaderRef, inView } = useInView({ rootMargin: "200px" });
  const fetchPeople = useCallback(
    (cursor?: string | null) => getPeople(cursor ?? undefined),
    [],
  );
  const { items: people, hasMore, isLoading, error } = useListStore(
    (state) =>
      (state.lists[PEOPLE_LIST_KEY] as
        | ListState<PersonReadSimple>
        | undefined) ?? (defaultListState as ListState<PersonReadSimple>),
  );
  const { fetchInitial, loadMore, clearList, removeItems } = useListStore();

  const refreshKey = useTaskCompletionVersion([
    "process_media",
    "cluster_persons",
  ]);
  const [seenRefreshKey, setSeenRefreshKey] = useState(refreshKey);
  const hasNewItems = refreshKey !== seenRefreshKey;
  const {
    selectionMode,
    selectedIds,
    toggleMode,
    toggle,
    clear,
    setSelected,
    pruneTo,
  } = usePeopleSelection();
  const [isDeleting, setIsDeleting] = useState(false);
  const [isMerging, setIsMerging] = useState(false);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [mergeOpen, setMergeOpen] = useState(false);
  const [snackbar, setSnackbar] = useState<{
    open: boolean;
    message: string;
    severity: "success" | "error";
  }>({ open: false, message: "", severity: "success" });

  useEffect(() => {
    void fetchInitial(PEOPLE_LIST_KEY, () => fetchPeople(null));
  }, [fetchInitial, fetchPeople]);

  useEffect(() => {
    if (inView && hasMore && !isLoading && !error) {
      void loadMore(PEOPLE_LIST_KEY, fetchPeople);
    }
  }, [inView, hasMore, isLoading, error, loadMore, fetchPeople]);

  useEffect(() => {
    pruneTo(people.map((person) => person.id));
  }, [people, pruneTo]);

  const selectedPeople = useMemo(() => {
    const selected = selectedIds;
    return people.filter((person) => selected.has(person.id));
  }, [people, selectedIds]);

  if (!config.ENABLE_PEOPLE) {
    return (
      <Typography variant="h5" color="text.primary" gutterBottom>
        People disabled!
      </Typography>
    );
  }

  if (isLoading && people.length === 0) {
    return (
      <Box textAlign="center" py={4}>
        <CircularProgress color="secondary" />
      </Box>
    );
  }

  const refetch = () => {
    clearList(PEOPLE_LIST_KEY);
    void fetchInitial(PEOPLE_LIST_KEY, () => fetchPeople(null));
  };

  const handleRefresh = () => {
    setSeenRefreshKey(refreshKey);
    refetch();
  };

  const handleDeleteSelected = () => {
    if (selectionMode && selectedIds.size > 0) {
      setConfirmDeleteOpen(true);
    }
  };

  const handleConfirmDeleteSelected = async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) {
      setConfirmDeleteOpen(false);
      return;
    }

    setIsDeleting(true);
    try {
      const result = await deletePersonsBulk(ids);
      const deletedCount = result.deleted_ids.length;
      removeItems(PEOPLE_LIST_KEY, result.deleted_ids);

      const deletedIds = new Set(result.deleted_ids);
      const remaining = ids.filter((id) => !deletedIds.has(id));
      setSelected(remaining);
      if (remaining.length === 0) {
        toggleMode();
      }

      const parts: string[] = [];
      if (deletedCount > 0) {
        parts.push(
          `Deleted ${deletedCount} person${deletedCount === 1 ? "" : "s"}.`,
        );
      }
      if (result.skipped_ids.length > 0) {
        parts.push(
          `${result.skipped_ids.length} person${result.skipped_ids.length === 1 ? "" : "s"} skipped.`,
        );
      }
      setSnackbar({
        open: true,
        message: parts.join(" ") || "No people were deleted.",
        severity: deletedCount > 0 ? "success" : "error",
      });
    } catch (caught) {
      console.error("Failed to delete selected people", caught);
      setSnackbar({
        open: true,
        message: "Failed to delete selected people",
        severity: "error",
      });
    } finally {
      setIsDeleting(false);
      setConfirmDeleteOpen(false);
    }
  };

  const handleMerge = async (target: PersonReadSimple | Person) => {
    const sourceIds = Array.from(selectedIds).filter((id) => id !== target.id);
    if (sourceIds.length === 0) return;

    setIsMerging(true);
    try {
      const result = await mergeMultiplePersons(target.id, sourceIds);
      removeItems(PEOPLE_LIST_KEY, result.merged_ids);

      const mergedIds = new Set(result.merged_ids);
      const addedAppearances = selectedPeople.reduce(
        (sum, person) =>
          mergedIds.has(person.id)
            ? sum + (person.appearance_count ?? 0)
            : sum,
        0,
      );
      if (addedAppearances > 0) {
        useListStore.setState((state) => {
          const list = state.lists[PEOPLE_LIST_KEY];
          if (!list) return state;
          return {
            lists: {
              ...state.lists,
              [PEOPLE_LIST_KEY]: {
                ...list,
                items: list.items.map((person: PersonReadSimple) =>
                  person.id === target.id
                    ? {
                        ...person,
                        appearance_count:
                          (person.appearance_count ?? 0) + addedAppearances,
                      }
                    : person,
                ),
              },
            },
          };
        });
      }

      const remaining = Array.from(selectedIds).filter(
        (id) => !mergedIds.has(id),
      );
      setSelected(remaining);
      setMergeOpen(false);
      if (remaining.length < 2) {
        clear();
      }

      const mergedCount = result.merged_ids.length;
      const skippedCount = result.skipped_ids.length;
      setSnackbar({
        open: true,
        message: `Merged ${mergedCount} person${mergedCount === 1 ? "" : "s"}; skipped ${skippedCount}.`,
        severity: mergedCount > 0 ? "success" : "error",
      });
    } catch (caught) {
      console.error("Failed to merge selected people", caught);
      setSnackbar({
        open: true,
        message: "Failed to merge selected people",
        severity: "error",
      });
    } finally {
      setIsMerging(false);
    }
  };

  const handleCloseSnackbar = () =>
    setSnackbar((previous) => ({ ...previous, open: false }));

  const selectedCount = selectedIds.size;

  return (
    <Container
      maxWidth={false}
      sx={{ pt: 4, pb: 6, bgcolor: "background.default", px: 4 }}
    >
      <Stack
        direction={{ xs: "column", sm: "row" }}
        spacing={2}
        justifyContent="space-between"
        alignItems={{ xs: "flex-start", sm: "center" }}
        sx={{ mb: 3 }}
      >
        <Typography variant="h5" color="text.primary">
          People
        </Typography>
        <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap">
          {hasNewItems && (
            <Chip
              color="primary"
              variant="outlined"
              icon={<RefreshIcon />}
              label="New items — Refresh"
              onClick={handleRefresh}
            />
          )}
          {selectionMode && (
            <Typography variant="body2" color="text.secondary">
              {selectedCount} selected
            </Typography>
          )}
          <Button variant="outlined" size="small" onClick={toggleMode}>
            {selectionMode ? "Cancel Selection" : "Select People"}
          </Button>
          <Button
            variant="contained"
            size="small"
            onClick={() => setMergeOpen(true)}
            disabled={!selectionMode || selectedCount < 2 || isMerging}
          >
            Merge Selected
          </Button>
          <Button
            variant="contained"
            color="error"
            size="small"
            onClick={handleDeleteSelected}
            disabled={!selectionMode || selectedCount === 0 || isDeleting}
          >
            {isDeleting ? "Deleting..." : "Delete Selected"}
          </Button>
        </Stack>
      </Stack>

      {error && (
        <Alert
          severity="error"
          sx={{ mb: 3 }}
          action={
            <Button color="inherit" size="small" onClick={refetch}>
              Retry
            </Button>
          }
        >
          Failed to load people: {error}
        </Alert>
      )}

      <Grid container spacing={3} alignItems="stretch">
        {people.map((person) => (
          <Grid key={person.id} size={{ xs: 6, sm: 4, md: 2, lg: 1.5 }}>
            <PersonCard
              person={person}
              selectable={selectionMode}
              selected={selectionMode && selectedIds.has(person.id)}
              onToggleSelect={toggle}
            />
          </Grid>
        ))}
      </Grid>

      {isLoading && people.length > 0 && (
        <Box textAlign="center" py={2}>
          <CircularProgress color="secondary" />
        </Box>
      )}

      {hasMore && !error && <Box ref={loaderRef} sx={{ height: 10 }} />}

      <ConfirmDialog
        open={confirmDeleteOpen}
        title="Delete Selected People"
        message={`Delete ${selectedCount} selected person${selectedCount === 1 ? "" : "s"}? This cannot be undone.`}
        confirmLabel="Delete"
        loading={isDeleting}
        onConfirm={handleConfirmDeleteSelected}
        onClose={() => setConfirmDeleteOpen(false)}
      />

      <MergePeopleDialog
        open={mergeOpen}
        selectedPeople={selectedPeople}
        merging={isMerging}
        onClose={() => setMergeOpen(false)}
        onConfirm={handleMerge}
      />

      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={handleCloseSnackbar}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert
          onClose={handleCloseSnackbar}
          severity={snackbar.severity}
          sx={{ width: "100%" }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Container>
  );
}
