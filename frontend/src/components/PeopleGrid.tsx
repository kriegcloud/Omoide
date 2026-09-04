import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  ToggleButton,
  ToggleButtonGroup,
} from "@mui/material";
import Grid from "@mui/material/Grid";
import { useInView } from "react-intersection-observer";
import config from "../config";
import { usePeopleSelection } from "../hooks/usePeopleSelection";
import { useMarqueeSelection } from "../hooks/useMarqueeSelection";
import { getPeople } from "../services/person";
import {
  deletePersonsBulk,
  hidePersonsBulk,
  mergeMultiplePersons,
  unhidePersonsBulk,
} from "../services/personActions";
import {
  defaultListState,
  ListState,
  useListStore,
} from "../stores/useListStore";
import { useTaskCompletionVersion } from "../TaskEventsContext";
import { Person, PersonReadSimple } from "../types";
import ConfirmDialog from "./ConfirmDialog";
import MergePeopleDialog from "./MergePeopleDialog";
import MarqueeSelectionBox from "./MarqueeSelectionBox";
import PersonCard from "./PersonCard";

interface PeopleGridProps {
  title: string;
  listKey: string;
  hidden?: boolean;
  gender?: "female" | "male";
  onGenderChange?: (gender?: "female" | "male") => void;
}

type SnackbarState = {
  open: boolean;
  message: string;
  severity: "success" | "error";
};

export default function PeopleGrid({
  title,
  listKey,
  hidden = false,
  gender,
  onGenderChange,
}: PeopleGridProps) {
  const { ref: loaderRef, inView } = useInView({ rootMargin: "200px" });
  const fetchPeople = useCallback(
    (cursor?: string | null) => getPeople(cursor ?? undefined, hidden, gender),
    [gender, hidden],
  );
  const { items: people, hasMore, isLoading, error } = useListStore(
    (state) =>
      (state.lists[listKey] as ListState<PersonReadSimple> | undefined) ??
      (defaultListState as ListState<PersonReadSimple>),
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
  const [isChangingVisibility, setIsChangingVisibility] = useState(false);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [mergeOpen, setMergeOpen] = useState(false);
  const [snackbar, setSnackbar] = useState<SnackbarState>({
    open: false,
    message: "",
    severity: "success",
  });
  const gridRef = useRef<HTMLDivElement>(null);
  const { marqueeRect, onItemClick } = useMarqueeSelection<number>({
    containerRef: gridRef,
    itemSelector: "[data-selectable-id]",
    getId: (element) => Number(element.dataset.selectableId),
    enabled: selectionMode,
    selectedIds,
    onSelectionChange: setSelected,
  });

  useEffect(() => {
    void fetchInitial(listKey, () => fetchPeople(null));
  }, [fetchInitial, fetchPeople, listKey]);

  useEffect(() => {
    if (inView && hasMore && !isLoading && !error) {
      void loadMore(listKey, fetchPeople);
    }
  }, [inView, hasMore, isLoading, error, loadMore, fetchPeople, listKey]);

  useEffect(() => {
    pruneTo(people.map((person) => person.id));
  }, [people, pruneTo]);

  const selectedPeople = useMemo(
    () => people.filter((person) => selectedIds.has(person.id)),
    [people, selectedIds],
  );
  const selectedCount = selectedIds.size;

  const refetch = () => {
    clearList(listKey);
    void fetchInitial(listKey, () => fetchPeople(null));
  };

  const handleRefresh = () => {
    setSeenRefreshKey(refreshKey);
    refetch();
  };

  const updateSelectionAfterRemoval = (removedIds: number[]) => {
    const removed = new Set(removedIds);
    const remaining = Array.from(selectedIds).filter((id) => !removed.has(id));
    setSelected(remaining);
    if (remaining.length === 0 && selectionMode) {
      toggleMode();
    }
  };

  const handleVisibilityChange = async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;

    setIsChangingVisibility(true);
    try {
      const result = hidden
        ? await unhidePersonsBulk(ids)
        : await hidePersonsBulk(ids);
      const changedIds = hidden ? result.unhidden_ids : result.hidden_ids;
      removeItems(listKey, changedIds);
      updateSelectionAfterRemoval(changedIds);
      const action = hidden ? "Unhidden" : "Hidden";
      const parts = [
        `${action} ${changedIds.length} person${changedIds.length === 1 ? "" : "s"}.`,
      ];
      if (result.skipped_ids.length > 0) {
        parts.push(`${result.skipped_ids.length} skipped.`);
      }
      setSnackbar({
        open: true,
        message: parts.join(" "),
        severity: changedIds.length > 0 ? "success" : "error",
      });
    } catch (caught) {
      console.error("Failed to update selected people visibility", caught);
      setSnackbar({
        open: true,
        message: hidden
          ? "Failed to unhide selected people"
          : "Failed to hide selected people",
        severity: "error",
      });
    } finally {
      setIsChangingVisibility(false);
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
      removeItems(listKey, result.deleted_ids);
      updateSelectionAfterRemoval(result.deleted_ids);
      const deletedCount = result.deleted_ids.length;
      const parts: string[] = [];
      if (deletedCount > 0) {
        parts.push(
          `Deleted ${deletedCount} person${deletedCount === 1 ? "" : "s"}.`,
        );
      }
      if (result.skipped_ids.length > 0) {
        parts.push(`${result.skipped_ids.length} skipped.`);
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
      removeItems(listKey, result.merged_ids);
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
          const list = state.lists[listKey];
          if (!list) return state;
          return {
            lists: {
              ...state.lists,
              [listKey]: {
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
      updateSelectionAfterRemoval(result.merged_ids);
      setMergeOpen(false);
      if (selectedIds.size - result.merged_ids.length < 2) {
        clear();
      }
      setSnackbar({
        open: true,
        message: `Merged ${result.merged_ids.length} person${result.merged_ids.length === 1 ? "" : "s"}; skipped ${result.skipped_ids.length}.`,
        severity: result.merged_ids.length > 0 ? "success" : "error",
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

  const handleCloseSnackbar = () =>
    setSnackbar((previous) => ({ ...previous, open: false }));

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
          {title}
        </Typography>
        <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap">
          {onGenderChange && (
            <ToggleButtonGroup
              exclusive
              size="small"
              value={gender ?? "all"}
              onChange={(_, value: "all" | "female" | "male" | null) => {
                if (value) onGenderChange(value === "all" ? undefined : value);
              }}
              aria-label="Filter people by gender"
            >
              <ToggleButton value="all">All</ToggleButton>
              <ToggleButton value="female">Female</ToggleButton>
              <ToggleButton value="male">Male</ToggleButton>
            </ToggleButtonGroup>
          )}
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
          {!hidden && (
            <Button
              variant="contained"
              size="small"
              onClick={() => setMergeOpen(true)}
              disabled={!selectionMode || selectedCount < 2 || isMerging}
            >
              Merge Selected
            </Button>
          )}
          <Button
            variant="contained"
            color="warning"
            size="small"
            onClick={handleVisibilityChange}
            disabled={
              !selectionMode || selectedCount === 0 || isChangingVisibility
            }
          >
            {isChangingVisibility
              ? hidden
                ? "Unhiding..."
                : "Hiding..."
              : hidden
                ? "Unhide Selected"
                : "Hide Selected"}
          </Button>
          <Button
            variant="contained"
            color="error"
            size="small"
            onClick={() => setConfirmDeleteOpen(true)}
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

      <Grid
        ref={gridRef}
        container
        spacing={3}
        alignItems="stretch"
        sx={{ position: "relative" }}
      >
        {people.map((person) => (
          <Grid key={person.id} size={{ xs: 6, sm: 4, md: 2, lg: 1.5 }}>
            <PersonCard
              person={person}
              selectable={selectionMode}
              selected={selectionMode && selectedIds.has(person.id)}
              onToggleSelect={(personId, event) => {
                if (event) onItemClick(personId, event);
                else toggle(personId);
              }}
            />
          </Grid>
        ))}
        <MarqueeSelectionBox container={gridRef.current} rect={marqueeRect} />
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

      {!hidden && (
        <MergePeopleDialog
          open={mergeOpen}
          selectedPeople={selectedPeople}
          merging={isMerging}
          onClose={() => setMergeOpen(false)}
          onConfirm={handleMerge}
        />
      )}

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
