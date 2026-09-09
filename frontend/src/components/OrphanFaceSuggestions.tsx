import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Container,
  Paper,
  Slider,
  Snackbar,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import { useInView } from "react-intersection-observer";
import { useUndo, useUndoRefresh } from "../context/UndoContext";
import { assignSuggestedFaces, getOrphanFaceSuggestions } from "../services/face";
import { detachFace } from "../services/faceActions";
import { defaultListState, refreshCachedList, useListStore, type ListState } from "../stores/useListStore";
import type { OrphanFaceSuggestion, SuggestedFaceAssignment } from "../types";
import ConfirmDialog from "./ConfirmDialog";
import { FaceGrid } from "./FaceGrid";

type SuggestionItem = OrphanFaceSuggestion & { id: number };

const skippedReasons = {
  unknown_face: "face no longer exists",
  face_already_assigned: "face is already assigned",
  unknown_person: "suggested person no longer exists",
};

export default function OrphanFaceSuggestions({ minScore, onMinScoreChange }: {
  minScore: number;
  onMinScoreChange: (value: number) => void;
}) {
  const listKey = `orphan-face-suggestions:${minScore.toFixed(2)}`;
  const { items, hasMore, isLoading, error }: ListState<SuggestionItem> = useListStore(
    (state) => state.lists[listKey] || defaultListState,
  );
  const { clearList, fetchInitial, loadMore, removeItems } = useListStore();
  const { push, refreshVisible } = useUndo();
  const [sliderScore, setSliderScore] = useState(minScore);
  const [selectedFaceIds, setSelectedFaceIds] = useState<number[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const processing = useRef(false);
  const [pendingAssignments, setPendingAssignments] = useState<SuggestedFaceAssignment[] | null>(null);
  const [snackbar, setSnackbar] = useState<{
    message: string;
    severity: "success" | "warning" | "error";
  } | null>(null);

  const fetchPage = useCallback(async (cursor: string | null = null) => {
    const page = await getOrphanFaceSuggestions(cursor, 48, minScore);
    return { ...page, items: page.items.map((item) => ({ ...item, id: item.face.id })) };
  }, [minScore]);

  useEffect(() => {
    setSliderScore(minScore);
    setSelectedFaceIds([]);
    setPendingAssignments(null);
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

  const { ref: loaderRef, inView } = useInView({
    threshold: 0.5,
    skip: isLoading || !hasMore || !!error || isProcessing || pendingAssignments !== null,
    rootMargin: "0px 0px 200px 0px",
  });
  useEffect(() => {
    if (inView && !error && !isProcessing && pendingAssignments === null) {
      void loadMore(listKey, fetchPage);
    }
  }, [inView, error, isProcessing, pendingAssignments, loadMore, listKey, fetchPage]);

  const faces = useMemo(() => items.map((item) => item.face), [items]);
  const suggestionsById = useMemo(() => new Map(items.map((item) => [item.id, item])), [items]);
  const aboveMin = items.filter((item) => item.score >= sliderScore);
  const selectedItems = items.filter((item) => selectedFaceIds.includes(item.id));
  const actionsDisabled = isProcessing || sliderScore !== minScore;

  const accept = async (assignments: SuggestedFaceAssignment[]) => {
    if (processing.current || !assignments.length) return;
    processing.current = true;
    setIsProcessing(true);
    try {
      const result = await assignSuggestedFaces(assignments);
      const skippedIds = new Set(result.skipped.map((item) => item.face_id));
      const acceptedIds = assignments.map((item) => item.face_id).filter((id) => !skippedIds.has(id));
      if (acceptedIds.length) {
        push({
          label: `Accepted ${result.assigned} face suggestion(s)`,
          undo: async () => {
            await detachFace(acceptedIds);
            await refreshVisible();
          },
        });
        removeItems(listKey, acceptedIds);
      }
      setSelectedFaceIds([]);
      const message = `Accepted ${result.assigned} suggestion(s).${result.skipped.length
        ? ` Skipped ${result.skipped.length}: ${result.skipped.map((item) => `Face ${item.face_id}: ${skippedReasons[item.reason]}`).join("; ")}.`
        : ""}`;
      setSnackbar({ message, severity: result.skipped.length ? "warning" : "success" });
      try {
        // The cursor is a ranking offset; assignment changes both membership and
        // prototypes. Restart pagination instead of continuing a stale offset.
        await refreshVisible();
      } catch {
        setSnackbar({ message: `${message} Could not refresh the list. Please retry.`, severity: "warning" });
      }
    } catch (reason) {
      setSnackbar({
        message: reason instanceof Error ? reason.message : "Failed to accept suggestions.",
        severity: "error",
      });
    } finally {
      processing.current = false;
      setIsProcessing(false);
      setPendingAssignments(null);
    }
  };

  const retry = () => {
    setSelectedFaceIds([]);
    clearList(listKey);
    void fetchInitial(listKey, () => fetchPage());
  };
  const toAssignments = (suggestions: SuggestionItem[]): SuggestedFaceAssignment[] =>
    suggestions.map((item) => ({ face_id: item.id, person_id: item.person_id }));

  return (
    <Container id="unassigned-faces" maxWidth="xl" sx={{ pt: 4, pb: 7 }}>
      <Typography variant="h4" sx={{ mb: 2 }}>Suggested Matches</Typography>
      <Box sx={{ maxWidth: 400, px: 1, mb: 2 }}>
        <Typography id="suggestions-min-score">Minimum score: {sliderScore.toFixed(2)}</Typography>
        <Slider
          aria-labelledby="suggestions-min-score"
          getAriaValueText={(value) => value.toFixed(2)}
          min={0.4}
          max={0.8}
          step={0.01}
          value={sliderScore}
          valueLabelDisplay="auto"
          valueLabelFormat={(value) => value.toFixed(2)}
          marks={[{ value: 0.4, label: "0.40" }, { value: 0.8, label: "0.80" }]}
          disabled={isProcessing || pendingAssignments !== null}
          onChange={(_, value) => setSliderScore(value as number)}
          onChangeCommitted={(_, value) => onMinScoreChange(value as number)}
        />
      </Box>
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
      {error && (
        <Alert severity="error" sx={{ mb: 2 }} action={<Button color="inherit" onClick={retry} disabled={isProcessing}>Retry</Button>}>
          Failed to load suggestions: {error}
        </Alert>
      )}
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
                {suggestion.pose_bin}
              </Typography>
            </Box>
          );
        }}
      />
      {isLoading && <Box textAlign="center" py={4}><CircularProgress aria-label="Loading suggestions" /></Box>}
      {!items.length && !isLoading && !error && (
        <Typography align="center" sx={{ py: 4 }}>No suggestions at or above {minScore.toFixed(2)}. Try lowering the minimum score.</Typography>
      )}
      {hasMore && <Box ref={loaderRef} sx={{ height: 50 }} />}
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
        autoHideDuration={snackbar?.severity === "success" ? 4000 : null}
        onClose={(_, reason) => { if (reason !== "clickaway") setSnackbar(null); }}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert severity={snackbar?.severity} onClose={() => setSnackbar(null)} sx={{ maxHeight: 240, overflow: "auto" }}>
          {snackbar?.message}
        </Alert>
      </Snackbar>
    </Container>
  );
}
