import { useCallback, useEffect, useRef, useState } from "react";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  Paper,
  Slider,
  Snackbar,
  Stack,
  Typography,
} from "@mui/material";
import config, { API } from "../config";
import { useUndo, useUndoRefresh } from "../context/UndoContext";
import { useLocalStorageBoolean } from "../hooks/useLocalStorageBoolean";
import {
  createPairDecision,
  deletePairDecision,
  getMergeCandidates,
  mergePersons,
} from "../services/personActions";
import type { MergeCandidate, MergeCandidatePerson } from "../types";
import { encodeFilePath } from "../urlUtils";
import ConfirmDialog from "./ConfirmDialog";
import SelectableTileFrame from "./SelectableTileFrame";

interface MergeQueueProps {
  onMerged: () => void;
  refreshVersion: number;
}

const personLabel = (person: MergeCandidatePerson) =>
  `${person.name || "Unknown"} (#${person.id})`;

function PairPerson({ person }: { person: MergeCandidatePerson }) {
  const thumbUrl = person.profile_face?.thumbnail_path
    ? `${API}/thumbnails/${encodeFilePath(person.profile_face.thumbnail_path)}`
    : undefined;

  return (
    <SelectableTileFrame
      id={person.id}
      href={`/person/${person.id}`}
      selecting={false}
      selected={false}
      selectionEnabled={false}
      aspectRatio={1}
      radius={1}
      sx={{ width: "100%", maxWidth: 140 }}
      linkFooter
      footer={
        <Box sx={{ p: 1 }}>
          <Typography variant="body2" sx={{ overflowWrap: "anywhere" }}>
            {person.name || `Unknown #${person.id}`}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {person.appearance_count ?? 0} media
          </Typography>
        </Box>
      }
    >
      {thumbUrl ? (
        <Box
          component="img"
          src={thumbUrl}
          alt={personLabel(person)}
          loading="lazy"
          sx={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
        />
      ) : (
        <Box sx={{ height: "100%", display: "grid", placeItems: "center", bgcolor: "action.hover" }}>
          <Typography variant="h4" aria-label={personLabel(person)}>
            {person.name?.trim().slice(0, 2).toUpperCase() || "?"}
          </Typography>
        </Box>
      )}
    </SelectableTileFrame>
  );
}

export default function MergeQueue({ onMerged, refreshVersion }: MergeQueueProps) {
  const { push, refreshVisible } = useUndo();
  const [collapsed, setCollapsed] = useLocalStorageBoolean("merge_queue_collapsed", false);
  const [minSimilarity, setMinSimilarity] = useState(60);
  const [sliderValue, setSliderValue] = useState(60);
  const [items, setItems] = useState<MergeCandidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [merge, setMerge] = useState<{
    source: MergeCandidatePerson;
    target: MergeCandidatePerson;
  } | null>(null);
  const [snackbar, setSnackbar] = useState<{
    message: string;
    severity: "success" | "error";
  } | null>(null);
  const request = useRef<AbortController | null>(null);
  const mounted = useRef(false);
  const mutating = useRef(false);

  const refresh = useCallback(async () => {
    if (!mounted.current) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setError(null);
    try {
      const page = await getMergeCandidates(minSimilarity, 50, controller.signal);
      if (!controller.signal.aborted) setItems(page.items);
    } catch (caught) {
      if (!controller.signal.aborted) {
        setError(caught instanceof Error ? caught.message : "Failed to load possible duplicates");
      }
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [minSimilarity]);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    return () => {
      mounted.current = false;
      request.current?.abort();
    };
  }, [refresh, refreshVersion]);
  useUndoRefresh("merge-queue", refresh);

  const handleNotSame = async (pair: MergeCandidate) => {
    if (mutating.current || config.PRESENTATION_MODE) return;
    mutating.current = true;
    setBusy(true);
    request.current?.abort();
    try {
      const decision = await createPairDecision({
        person_a_id: pair.person_a.id,
        person_b_id: pair.person_b.id,
        decision: "not_same",
      });
      setItems((previous) => previous.filter((item) =>
        item.person_a.id !== pair.person_a.id || item.person_b.id !== pair.person_b.id,
      ));
      setSnackbar(null);
      push({
        label: `${personLabel(pair.person_a)} and ${personLabel(pair.person_b)} marked as different people`,
        undo: async () => {
          await deletePairDecision(decision.id);
          await refreshVisible();
        },
      });
    } catch (caught) {
      setSnackbar({
        message: caught instanceof Error ? caught.message : "Failed to save pair decision",
        severity: "error",
      });
    } finally {
      await refresh();
      mutating.current = false;
      setBusy(false);
    }
  };

  const handleMerge = async () => {
    if (!merge || mutating.current || config.PRESENTATION_MODE) return;
    mutating.current = true;
    setBusy(true);
    request.current?.abort();
    try {
      await mergePersons(merge.source.id, merge.target.id);
      // Every row involving either person now needs fresh counts and scores.
      setItems((previous) => previous.filter((pair) =>
        ![pair.person_a.id, pair.person_b.id].some((id) =>
          id === merge.source.id || id === merge.target.id,
        ),
      ));
      setMerge(null);
      setSnackbar({
        message: `Merged ${personLabel(merge.source)} into ${personLabel(merge.target)}.`,
        severity: "success",
      });
      onMerged();
    } catch (caught) {
      setSnackbar({
        message: caught instanceof Error ? caught.message : "Failed to merge people",
        severity: "error",
      });
    } finally {
      await refresh();
      mutating.current = false;
      setBusy(false);
    }
  };

  const actionsDisabled = busy || loading || !!error || config.PRESENTATION_MODE;

  return (
    <Paper component="section" variant="outlined" aria-label="Possible duplicates" sx={{ p: 2, mb: 3 }}>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ sm: "center" }}>
        <Button
          color="inherit"
          onClick={() => setCollapsed(!collapsed)}
          aria-expanded={!collapsed}
          aria-controls="merge-queue-pairs"
          endIcon={collapsed ? <ExpandMoreIcon /> : <ExpandLessIcon />}
          sx={{ alignSelf: "flex-start", textTransform: "none" }}
        >
          <Typography component="span" variant="h6">Possible duplicates</Typography>
          <Chip
            component="span"
            size="small"
            label={loading ? "…" : error ? "?" : items.length}
            sx={{ ml: 1 }}
          />
        </Button>
        <Box sx={{ flexGrow: 1 }} />
        <Box sx={{ width: { xs: "100%", sm: 220 }, px: 1 }}>
          <Typography id="merge-queue-similarity" variant="caption" color="text.secondary">
            Minimum similarity: {sliderValue}%
          </Typography>
          <Slider
            aria-labelledby="merge-queue-similarity"
            getAriaValueText={(value) => `${value}%`}
            value={sliderValue}
            min={60}
            max={100}
            step={1}
            size="small"
            valueLabelDisplay="auto"
            disabled={busy || !!merge}
            onChange={(_, value) => setSliderValue(value as number)}
            onChangeCommitted={(_, value) => setMinSimilarity(value as number)}
          />
        </Box>
      </Stack>
      <Collapse in={!collapsed}>
        <Box id="merge-queue-pairs" aria-busy={loading} sx={{ mt: 2 }}>
          {error && (
            <Alert severity="error" action={<Button color="inherit" onClick={() => void refresh()}>Retry</Button>}>
              {error}
            </Alert>
          )}
          {loading && <Box role="status" sx={{ py: 2, textAlign: "center" }}><CircularProgress size={24} aria-label="Loading possible duplicates" /></Box>}
          {!loading && !error && items.length === 0 && (
            <Typography color="text.secondary">No possible duplicates at {minSimilarity}% similarity or higher.</Typography>
          )}
          {!loading && !error && items.length === 50 && (
            <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
              Showing the 50 most similar pairs. Review pairs to load more.
            </Typography>
          )}
          <Stack spacing={2} divider={<Box sx={{ borderBottom: 1, borderColor: "divider" }} />} sx={{ maxHeight: 560, overflowY: "auto" }}>
            {items.map((pair) => (
              <Stack key={`${pair.person_a.id}-${pair.person_b.id}`} direction={{ xs: "column", md: "row" }} spacing={2} alignItems={{ md: "center" }} sx={{ p: 1 }}>
                <Box sx={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 140px))", gap: 2 }}>
                  <PairPerson person={pair.person_a} />
                  <PairPerson person={pair.person_b} />
                </Box>
                <Stack spacing={1} alignItems="flex-start" sx={{ flexGrow: 1 }}>
                  <Chip size="small" color="primary" variant="outlined" label={`${pair.similarity.toFixed(2)}% similar`} />
                  <Typography variant="caption" color="text.secondary">{pair.shared_media} shared media</Typography>
                </Stack>
                <Stack direction="row" useFlexGap flexWrap="wrap" spacing={1}>
                  <Button
                    variant="outlined"
                    size="small"
                    disabled={actionsDisabled}
                    aria-label={`Merge right into left: keep ${personLabel(pair.person_a)}`}
                    onClick={() => setMerge({ source: pair.person_b, target: pair.person_a })}
                  >
                    Merge ←
                  </Button>
                  <Button
                    variant="outlined"
                    size="small"
                    disabled={actionsDisabled}
                    aria-label={`Merge left into right: keep ${personLabel(pair.person_b)}`}
                    onClick={() => setMerge({ source: pair.person_a, target: pair.person_b })}
                  >
                    Merge →
                  </Button>
                  <Button size="small" disabled={actionsDisabled} onClick={() => void handleNotSame(pair)}>
                    Not the same
                  </Button>
                </Stack>
              </Stack>
            ))}
          </Stack>
        </Box>
      </Collapse>
      <ConfirmDialog
        open={!!merge}
        title="Merge people"
        message={merge ? `Merge ${personLabel(merge.source)} into ${personLabel(merge.target)}? ${personLabel(merge.target)} survives and keeps the combined appearances. ${personLabel(merge.source)} is removed. This cannot be undone.` : ""}
        confirmLabel="Merge"
        confirmColor="warning"
        loading={busy}
        onConfirm={() => void handleMerge()}
        onClose={() => setMerge(null)}
      />
      <Snackbar open={!!snackbar} autoHideDuration={4000} onClose={() => setSnackbar(null)} anchorOrigin={{ vertical: "bottom", horizontal: "center" }}>
        <Alert severity={snackbar?.severity} onClose={() => setSnackbar(null)} sx={{ width: "100%" }}>{snackbar?.message}</Alert>
      </Snackbar>
    </Paper>
  );
}
