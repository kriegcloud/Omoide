import { mutationBus } from "../stores/mutationBus";
import { appliedFaceIds, startLegacyFaceReview } from "../services/faceReview";
import type { AssignSuggestedFacesResult } from "../types";
import { useHotkey } from "../hotkeys/useHotkey";
import GalleryPage from "./GalleryPage";
import { useOptimisticCachedList } from "../stores/useListStore";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Paper,
  Slider,
  Snackbar,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import { useInView } from "react-intersection-observer";
import { useUndo, useUndoRefresh, useMutationRefresh } from "../context/UndoContext";
import { getOrphanFaceSuggestions } from "../services/face";
import { defaultListState, refreshCachedList, useListStore, useListInvalidation, type ListState } from "../stores/useListStore";
import type { OrphanFaceSuggestion, SuggestedFaceAssignment, Person } from "../types";
import ConfirmDialog from "./ConfirmDialog";
import { FaceGrid } from "./FaceGrid";
import PersonPicker, { PERSON_PICKER_DIALOG_CONTENT_SX } from "./PersonPicker";
import ListStatus from "./ListState";

type SuggestionItem = OrphanFaceSuggestion & { id: number; best_cos?: number; margin?: number; runner_up_person_cos?: number };
const reviewLaterKey = "omoide:orphan-faces:review-later";
function readReviewLater(): number[] {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(reviewLaterKey) ?? "[]");
    return Array.isArray(value) ? value.filter((id): id is number => typeof id === "number") : [];
  } catch { return []; }
}

export default function OrphanFaceSuggestions({ minScore, onMinScoreChange, tabs }: {
  tabs?: React.ReactNode;
  minScore: number;
  onMinScoreChange: (value: number) => void;
}) {
  const listKey = `orphan-face-suggestions:${minScore.toFixed(2)}`;
  const { items: cachedItems, hasMore, isLoading, error }: ListState<SuggestionItem> = useOptimisticCachedList<SuggestionItem>(listKey, "face") || defaultListState;
  const clearList = useListStore((state) => state.clearList);
  const fetchInitial = useListStore((state) => state.fetchInitial);
  const loadMore = useListStore((state) => state.loadMore);
  useListInvalidation(listKey);
  const [reviewLaterIds, setReviewLaterIds] = useState<number[]>(readReviewLater);
  const [dismissedPairs] = useState<string[]>([]);
  const [optimisticFaceIds] = useState<number[]>([]);
  const [alternativeFor, setAlternativeFor] = useState<SuggestionItem | null>(null);
  const items = useMemo(() => cachedItems.filter((item) =>
    !reviewLaterIds.includes(item.id) && !optimisticFaceIds.includes(item.id) && !dismissedPairs.includes(`${item.id}:${item.person_id}`)
  ), [cachedItems, reviewLaterIds, optimisticFaceIds, dismissedPairs]);
  const { push, refreshVisible } = useUndo();
  const [sliderScore, setSliderScore] = useState(minScore);
  const [selectedFaceIds, setSelectedFaceIds] = useState<number[]>([]);
  const isProcessing = false;
  const [pendingAssignments, setPendingAssignments] = useState<SuggestedFaceAssignment[] | null>(null);
  const [snackbar, setSnackbar] = useState<{
    message: string;
    severity: "warning" | "error";
  } | null>(null);

  const fetchPage = useCallback(async (cursor: string | null = null) => {
    const page = await getOrphanFaceSuggestions(cursor, 48, minScore);
    return { ...page, items: page.items.map((item) => ({ ...item, id: item.face.id })) };
  }, [minScore]);

  useEffect(() => {
    setSliderScore(minScore);
    setSelectedFaceIds([]);
    setPendingAssignments(null);
    setAlternativeFor(null);
    setSnackbar(null);
    clearList(listKey);
    void fetchInitial(listKey, () => fetchPage());
    // Invalidate in-flight pages when the filter changes or this view unmounts.
    return () => clearList(listKey);
  }, [clearList, fetchInitial, fetchPage, listKey, minScore]);

  useUndoRefresh(`cache:${listKey}`, async () => {
    setSelectedFaceIds([]);
    await refreshCachedList(listKey);
  });

  useMutationRefresh(["face:assigned", "face:detached", "face:deleted", "person:changed", "media:deleted"], () => refreshCachedList(listKey));

  const { ref: loaderRef, inView } = useInView({
    threshold: 0.5,
    skip: isLoading || !hasMore || !!error || isProcessing || pendingAssignments !== null,
    rootMargin: "0px 0px 200px 0px",
  });
  useEffect(() => {
    if (inView && hasMore && !isLoading && !error && !isProcessing && pendingAssignments === null) {
      void loadMore(listKey, fetchPage);
    }
  }, [inView, hasMore, isLoading, error, isProcessing, pendingAssignments, loadMore, listKey, fetchPage]);

  const faces = useMemo(() => items.map((item) => item.face), [items]);
  const suggestionsById = useMemo(() => new Map(items.map((item) => [item.id, item])), [items]);
  const aboveMin = items.filter((item) => item.score >= sliderScore);
  const selectedItems = items.filter((item) => selectedFaceIds.includes(item.id));
  const actionsDisabled = isProcessing || sliderScore !== minScore;

  const accept = async (assignments: SuggestedFaceAssignment[]) => {
    if (!assignments.length) return;
    const operation = startLegacyFaceReview<AssignSuggestedFacesResult>("Accept suggestions", assignments.map(item => item.face_id), "assign-suggested", { assignments, source: "suggestion" }, {
      receipt: (result, operation_id) => {
        const skipped = result.skipped.map(item => ({ key: `face:${item.face_id}`, reason: item.reason }));
        const skippedIds = new Set(result.skipped.map(item => item.face_id));
        const applied = assignments.filter(item => !skippedIds.has(item.face_id)).map(item => `face:${item.face_id}`);
        return { operation_id, state: skipped.length ? applied.length ? "partial" : "failed" : "succeeded", applied, skipped, failed: [], unknown: [] };
      },
    });
    setSelectedFaceIds([]);
    setPendingAssignments(null);
    const record = await operation.done;
    if (record.receipt?.applied.length) push({ label: `Assigned ${record.receipt.applied.length} faces`, undo: async () => {
      const undo = startLegacyFaceReview("Undo suggestion assignment", appliedFaceIds(record.receipt!), "detach", { face_ids: appliedFaceIds(record.receipt!) });
      await undo.done;
      await refreshVisible();
    } });
  };

  const reject = async (suggestion: SuggestionItem) => {
    startLegacyFaceReview("Reject suggestion", [suggestion.id], `${suggestion.id}/reject-suggestion`, { person_id: suggestion.person_id }, { query: listKey });
    setSelectedFaceIds(previous => previous.filter(id => id !== suggestion.id));
  };

  const reviewLater = (faceId: number) => {
    setReviewLaterIds((previous) => {
      const next = [...new Set([...previous, faceId])];
      try { sessionStorage.setItem(reviewLaterKey, JSON.stringify(next)); } catch { /* Keep the mounted session usable when storage is unavailable. */ }
      return next;
    });
    setSelectedFaceIds((previous) => previous.filter((id) => id !== faceId));
  };

  const chooseAnother = async (person: Person) => {
    if (!alternativeFor) return;
    const faceId = alternativeFor.id;
    startLegacyFaceReview("Assign face", [faceId], "assign", { face_ids: [faceId], person_id: person.id, source: "manual" }, {
      query: listKey,
      onResult: () => {
        mutationBus.emit({ type: "list:invalidate", prefix: "orphan-face" });
        mutationBus.emit({ type: "person:changed", ids: [] });
      },
      // The legacy response acknowledges the transaction without member IDs.
      // Preserve manual provenance; never offer an inverse based on requested IDs.
      receipt: (_result, operation_id) => ({ operation_id, state: "succeeded", applied: [], skipped: [], failed: [], unknown: [] }),
    });
    setAlternativeFor(null);
    setSelectedFaceIds(previous => previous.filter(id => id !== faceId));
  };

  const retry = () => {
    setSelectedFaceIds([]);
    clearList(listKey);
    void fetchInitial(listKey, () => fetchPage());
  };
  const toAssignments = (suggestions: SuggestionItem[]): SuggestedFaceAssignment[] =>
    suggestions.map((item) => ({ face_id: item.id, person_id: item.person_id }));

  useHotkey({ key: "a" }, () => void accept(toAssignments(selectedItems)), { scope: "page", enabled: selectedItems.length > 0 && !actionsDisabled, description: "Accept selected face suggestions" });
  useHotkey({ key: "r" }, () => { if (selectedItems[0]) void reject(selectedItems[0]); }, { scope: "page", enabled: selectedItems.length === 1 && !actionsDisabled, description: "Reject selected face suggestion" });
  useHotkey({ key: "l" }, () => selectedItems.forEach(item => reviewLater(item.id)), { scope: "page", enabled: selectedItems.length > 0 && !actionsDisabled, description: "Review selected suggestions later" });

  return (
    <GalleryPage id="unassigned-faces" title="Suggested Matches" tabs={tabs} count={items.length} countLabel="faces" filters={(<Box sx={{ maxWidth: 400, px: 1, mb: 2 }}>
        <Typography id="suggestions-min-score">Minimum score: {sliderScore.toFixed(2)}</Typography>
        <Slider
          aria-labelledby="suggestions-min-score"
          getAriaValueText={(value) => value.toFixed(2)}
          min={0.48}
          max={1}
          step={0.01}
          value={sliderScore}
          valueLabelDisplay="auto"
          valueLabelFormat={(value) => value.toFixed(2)}
          marks={[{ value: 0.48, label: "0.48" }, { value: 1, label: "1.00" }]}
          disabled={isProcessing || pendingAssignments !== null}
          onChange={(_, value) => setSliderScore(value as number)}
          onChangeCommitted={(_, value) => onMinScoreChange(value as number)}
        />
      </Box>)}>

      <Paper elevation={2} sx={{ p: 1.5, mb: 2 }}>
        <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" alignItems="center">
          <Typography role="status" variant="body2">
            {items.length} suggestions loaded · {aboveMin.length} at or above {sliderScore.toFixed(2)} · {selectedItems.length} selected
          </Typography>
          {hasMore && <Chip size="small" label="Load more to review the rest" />}
          <Box sx={{ flexGrow: 1 }} />
          <Button
            size="small"
            disabled={!items.length || isProcessing}
            onClick={() => setSelectedFaceIds(selectedItems.length < items.length ? items.map((item) => item.id) : [])}
          >
            {selectedItems.length < items.length ? "Select All" : "Select None"}
          </Button>
          <Button
            variant="contained"
            size="small"
            disabled={actionsDisabled || !selectedItems.length}
            onClick={() => void accept(toAssignments(selectedItems))}
          >
            Accept selected
          </Button>
          <Button
            variant="outlined"
            size="small"
            disabled={actionsDisabled || !aboveMin.length}
            onClick={() => setPendingAssignments(toAssignments(aboveMin))}
          >
            Accept all above {sliderScore.toFixed(2)}
          </Button>
          {isProcessing && <CircularProgress size={20} aria-label="Accepting suggestions" />}
        </Stack>
      </Paper>
      <ListStatus
        loading={isLoading && cachedItems.length === 0}
        error={error ? `Failed to load suggestions: ${error}` : null}
        empty={!items.length && !isLoading}
        emptyMessage={`No suggestions at or above ${minScore.toFixed(2)}. Try lowering the minimum score.`}
        onRetry={retry}
      />
      <FaceGrid
        faces={faces}
        selectedFaceIds={selectedFaceIds}
        onSelectionChange={setSelectedFaceIds}
        renderFooter={(face) => {
          const suggestion = suggestionsById.get(face.id)!;
          const personName = suggestion.person_name || `Person ${suggestion.person_id}`;
          return (
            <Box sx={{ p: 0.75 }}>
              <Tooltip title={`${personName} · ${suggestion.score.toFixed(2)}`}>
                <Chip
                  component={RouterLink}
                  to={`/person/${suggestion.person_id}`}
                  clickable
                  data-tile-control
                  data-no-marquee
                  size="small"
                  label={(
                    <>
                      <Box component="span" sx={{ overflow: "hidden", textOverflow: "ellipsis" }}>{personName}</Box>
                      <Box component="span" sx={{ flexShrink: 0 }}>· {suggestion.score.toFixed(2)}</Box>
                    </>
                  )}
                  sx={{ maxWidth: "100%", "& .MuiChip-label": { display: "flex", gap: 0.5, minWidth: 0 } }}
                />
              </Tooltip>
              <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.25 }}>
                {suggestion.pose_bin} · best cosine {(suggestion.best_cos ?? suggestion.score).toFixed(2)}
                {suggestion.margin != null && ` · margin ${suggestion.margin.toFixed(2)}`}
                {suggestion.runner_up_person_cos != null && ` · runner-up ${suggestion.runner_up_person_cos.toFixed(2)}`}
              </Typography>
              <Stack data-tile-control data-no-marquee spacing={0.5} sx={{ mt: 0.75 }}>
                <Button size="small" disabled={actionsDisabled} onClick={() => reject(suggestion)}>Not this person</Button>
                <Button size="small" disabled={actionsDisabled} onClick={() => setAlternativeFor(suggestion)}>Choose another…</Button>
                <Button size="small" disabled={actionsDisabled} onClick={() => reviewLater(suggestion.id)}>Review later</Button>
              </Stack>
            </Box>
          );
        }}
      />
      {isLoading && <Box textAlign="center" py={4}><CircularProgress aria-label="Loading suggestions" /></Box>}
      {hasMore && <Box ref={loaderRef} sx={{ height: 50 }} />}
      <Dialog open={alternativeFor !== null} onClose={isProcessing ? undefined : () => setAlternativeFor(null)} fullWidth>
        <DialogTitle>Choose another person</DialogTitle>
        <DialogContent sx={PERSON_PICKER_DIALOG_CONTENT_SX}>
          {alternativeFor && <PersonPicker autoFocus disabled={isProcessing} excludeIds={[alternativeFor.person_id]} onSelect={chooseAnother} />}
        </DialogContent>
        <DialogActions><Button disabled={isProcessing} onClick={() => setAlternativeFor(null)}>Cancel</Button></DialogActions>
      </Dialog>
      <ConfirmDialog
        open={pendingAssignments !== null}
        title="Accept suggested matches"
        message={`Assign these ${pendingAssignments?.length ?? 0} loaded faces with scores at or above ${minScore.toFixed(2)} to their suggested people? You can undo this assignment.`}
        confirmLabel={`Accept ${pendingAssignments?.length ?? 0} suggestions`}
        confirmColor="primary"
        loading={isProcessing}
        onConfirm={() => void accept(pendingAssignments ?? [])}
        onClose={() => setPendingAssignments(null)}
      />
      <Snackbar
        open={snackbar !== null}
        autoHideDuration={null}
        onClose={(_, reason) => { if (reason !== "clickaway") setSnackbar(null); }}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert severity={snackbar?.severity} onClose={() => setSnackbar(null)} sx={{ maxHeight: 240, overflow: "auto" }}>
          {snackbar?.message}
        </Alert>
      </Snackbar>
    </GalleryPage>
  );
}
