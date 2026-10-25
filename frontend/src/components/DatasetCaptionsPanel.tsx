import { useVisiblePolling } from "../hooks/useVisiblePolling";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  FormControl,
  InputLabel,
  LinearProgress,
  MenuItem,
  Paper,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { API } from "../config";
import {
  generateDatasetCaptions,
  getDatasetCaptions,
  markDatasetCaptionReviewed,
  updateDatasetCaption,
} from "../services/datasets";
import {
  approveAnnotation,
  getMediaAnnotations,
  startAnnotation,
} from "../services/annotations";
import { getTask } from "../services/task";
import type {
  DatasetCaption,
  DatasetCaptionFilter,
  Task,
} from "../types";
import { encodeFilePath } from "../urlUtils";

const FILTERS: Array<{ value: DatasetCaptionFilter; label: string }> = [
  { value: "all", label: "All" },
  { value: "findings", label: "Findings" },
  { value: "candidate", label: "Candidates" },
  { value: "approved", label: "Approved" },
  { value: "missing", label: "Missing" },
];

const TERMINAL_ATTEMPT_STATUSES = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "lost",
  "unknown",
]);

interface DatasetCaptionsPanelProps {
  datasetId: number;
}

export default function DatasetCaptionsPanel({
  datasetId,
}: DatasetCaptionsPanelProps) {
  const [filter, setFilter] = useState<DatasetCaptionFilter>("all");
  const [rows, setRows] = useState<DatasetCaption[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [busyItem, setBusyItem] = useState<number | null>(null);
  const [task, setTask] = useState<Task | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRefs = useRef<Array<HTMLTextAreaElement | HTMLInputElement | null>>(
    [],
  );

  const identity = `${datasetId}:${filter}`;
  const currentIdentity = useRef(identity); currentIdentity.current = identity;
  const generation = useRef(0);
  const readGeneration = useRef(0);
  const readController = useRef<AbortController | null>(null);
  const regeneration = useRef<AbortController | null>(null);
  const pendingMutation = useRef<symbol | null>(null);
  const morePending = useRef(false);
  const isCurrent = useCallback((version: number) => generation.current === version && currentIdentity.current === identity, [identity]);

  const load = useCallback(async () => {
    const version = generation.current;
    if (!isCurrent(version)) return;
    const revision = ++readGeneration.current;
    readController.current?.abort();
    const controller = new AbortController(); readController.current = controller;
    const current = () => isCurrent(version) && readGeneration.current === revision && !controller.signal.aborted;
    setLoading(true); setLoadingMore(false); morePending.current = false; setError(null);
    try {
      const page = await getDatasetCaptions(datasetId, filter, undefined, controller.signal);
      if (!current()) return;
      setRows(page.items); setNextCursor(page.next_cursor ?? null);
      setDrafts(Object.fromEntries(page.items.map(row => [row.item_id, row.caption])));
    } catch (reason) {
      if (current()) setError(reason instanceof Error ? reason.message : "Failed to load captions");
    } finally { if (current()) setLoading(false); }
  }, [datasetId, filter, isCurrent]);

  useEffect(() => {
    setTask(null);
  }, [datasetId]);
  useEffect(() => {
    generation.current += 1;
    setRows([]); setDrafts({}); setNextCursor(null); setBusyItem(null);
    pendingMutation.current = null; morePending.current = false;
    void load();
    return () => {
      generation.current += 1; readGeneration.current += 1;
      readController.current?.abort(); regeneration.current?.abort();
    };
  }, [load]);

  const pollTask = useCallback(async (visible: () => boolean) => {
    const version = generation.current;
    if (!task?.id || !isCurrent(version)) return;
    try {
      const next = await getTask(task.id);
      if (!visible() || !isCurrent(version)) return;
      setTask(next);
      if (!["pending", "running"].includes(next.status)) await load();
    } catch (reason) {
      if (visible() && isCurrent(version)) setError(reason instanceof Error ? reason.message : "Failed to refresh caption generation");
    }
  }, [task?.id, load, isCurrent]);
  useVisiblePolling(pollTask, 1500, Boolean(task && ["pending", "running"].includes(task.status)));

  const mutate = async (itemId: number, operation: (current: () => boolean) => Promise<void>, fallback: string) => {
    const version = generation.current;
    if (pendingMutation.current || !isCurrent(version)) return;
    const token = Symbol(); pendingMutation.current = token;
    const current = () => isCurrent(version) && pendingMutation.current === token;
    setBusyItem(itemId); setError(null);
    try { await operation(current); }
    catch (reason) { if (current()) setError(reason instanceof Error ? reason.message : fallback); }
    finally {
      if (current()) {pendingMutation.current = null; setBusyItem(null);}
    }
  };
  const save = async (row: DatasetCaption) => {
    const draft = drafts[row.item_id] ?? row.caption;
    await mutate(row.item_id, async current => {
      await updateDatasetCaption(datasetId, row.item_id, draft);
      if (current()) await load();
    }, "Failed to save caption");
  };
  const approve = async (row: DatasetCaption) => {
    if (!row.annotation_id || row.review_status !== "candidate") return;
    await mutate(row.item_id, async current => {
      await approveAnnotation(row.annotation_id!);
      if (!current()) return;
      await getMediaAnnotations(row.media_id);
      if (!current()) return;
      await markDatasetCaptionReviewed(datasetId, row.item_id);
      if (current()) await load();
    }, "Failed to approve caption");
  };
  const regenerate = async (row: DatasetCaption) => {
    await mutate(row.item_id, async current => {
      const controller = new AbortController(); regeneration.current = controller;
      const attempt = await startAnnotation(row.media_id, "caption");
      while (current() && !controller.signal.aborted) {
        await new Promise<void>(resolve => {
          const finish = () => {controller.signal.removeEventListener("abort", cancel); resolve();};
          const cancel = () => {window.clearTimeout(timer); finish();};
          const timer = window.setTimeout(finish, 1000);
          controller.signal.addEventListener("abort", cancel, {once: true});
          if (controller.signal.aborted) cancel();
        });
        if (!current() || controller.signal.aborted) return;
        const state = await getMediaAnnotations(row.media_id);
        if (!current() || controller.signal.aborted) return;
        const next = state.attempts.find(entry => entry.id === attempt.id);
        if (next && TERMINAL_ATTEMPT_STATUSES.has(next.status)) break;
      }
      if (current()) await load();
    }, "Failed to regenerate caption");
  };
  const generateMissing = async () => {
    await mutate(-1, async current => {
      const next = await generateDatasetCaptions(datasetId, true);
      if (current()) setTask(next);
    }, "Failed to generate captions");
  };
  const loadMore = async () => {
    const version = generation.current, revision = readGeneration.current;
    if (!nextCursor || morePending.current || !isCurrent(version)) return;
    const current = () => isCurrent(version) && readGeneration.current === revision;
    morePending.current = true; setLoadingMore(true); setError(null);
    try {
      const page = await getDatasetCaptions(datasetId, filter, nextCursor, readController.current?.signal);
      if (!current()) return;
      setRows(previous => {const seen = new Set(previous.map(row => row.item_id)); return [...previous, ...page.items.filter(row => !seen.has(row.item_id))];});
      setDrafts(previous => ({...Object.fromEntries(page.items.map(row => [row.item_id, row.caption])), ...previous}));
      setNextCursor(page.next_cursor ?? null);
    } catch (reason) {
      if (current()) setError(reason instanceof Error ? reason.message : "Failed to load more captions");
    } finally { if (current()) {morePending.current = false; setLoadingMore(false);} }
  };

  const taskActive = Boolean(
    task && ["pending", "running"].includes(task.status),
  );

  return (
    <Stack spacing={2}>
      {error && (
        <Alert severity="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
        <Button
          variant="contained"
          disabled={taskActive || busyItem !== null}
          onClick={() => void generateMissing()}
        >
          Generate missing
        </Button>
        <FormControl size="small" sx={{ minWidth: 180 }}>
          <InputLabel>Filter</InputLabel>
          <Select
            label="Filter"
            value={filter}
            onChange={(event) =>
              setFilter(event.target.value as DatasetCaptionFilter)
            }
          >
            {FILTERS.map((entry) => (
              <MenuItem key={entry.value} value={entry.value}>
                {entry.label}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </Stack>
      {task && (
        <Paper variant="outlined" sx={{ p: 1.5 }}>
          <Stack spacing={1}>
            <Typography variant="body2">
              Caption generation: {task.status} · {task.processed}/{task.total}
            </Typography>
            <LinearProgress
              variant={task.total ? "determinate" : "indeterminate"}
              value={task.total ? (task.processed / task.total) * 100 : undefined}
            />
            {!taskActive && task.result && (
              <Typography variant="caption" color="text.secondary">
                Generated {String(task.result.generated ?? 0)}, skipped{" "}
                {String(task.result.skipped ?? 0)}, failed{" "}
                {String(task.result.failed ?? 0)}
              </Typography>
            )}
          </Stack>
        </Paper>
      )}
      {loading ? (
        <Box display="grid" sx={{ placeItems: "center", py: 6 }}>
          <CircularProgress />
        </Box>
      ) : rows.length === 0 ? (
        <Typography color="text.secondary">
          No captions match this filter.
        </Typography>
      ) : (
        rows.map((row, index) => (
          <Paper
            key={row.item_id}
            variant="outlined"
            sx={{
              p: 2,
              contentVisibility: "auto",
              containIntrinsicSize: "168px",
            }}
          >
            <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
              <Box
                component="img"
                src={`${API}/thumbnails/${encodeFilePath(row.media.thumbnail_path ?? "")}`}
                alt=""
                sx={{
                  width: { xs: "100%", md: 150 },
                  height: 132,
                  objectFit: "cover",
                  borderRadius: 1,
                  opacity: row.excluded ? 0.45 : 1,
                }}
              />
              <Stack flex={1} spacing={1} minWidth={0}>
                <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                  <Chip size="small" label={row.source} />
                  {row.review_status && (
                    <Chip size="small" label={row.review_status} variant="outlined" />
                  )}
                  {row.findings.map((finding, findingIndex) => (
                    <Chip
                      key={`${finding.code}-${findingIndex}`}
                      size="small"
                      label={finding.code}
                      title={finding.message}
                      color={
                        finding.severity === "error"
                          ? "error"
                          : finding.severity === "warn"
                            ? "warning"
                            : "info"
                      }
                    />
                  ))}
                </Stack>
                <TextField
                  multiline
                  minRows={2}
                  value={drafts[row.item_id] ?? row.caption}
                  inputRef={(element) => {
                    inputRefs.current[index] = element;
                  }}
                  onChange={(event) =>
                    setDrafts((current) => ({
                      ...current,
                      [row.item_id]: event.target.value,
                    }))
                  }
                  onKeyDown={(event) => {
                    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                      event.preventDefault();
                      void approve(row);
                    } else if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      void save(row);
                    } else if (event.key === "ArrowDown" && !event.shiftKey) {
                      event.preventDefault();
                      inputRefs.current[index + 1]?.focus();
                    } else if (event.key === "ArrowUp" && !event.shiftKey) {
                      event.preventDefault();
                      inputRefs.current[index - 1]?.focus();
                    }
                  }}
                />
                <Stack direction="row" spacing={1}>
                  <Button
                    size="small"
                    disabled={busyItem !== null}
                    onClick={() => void save(row)}
                  >
                    Save
                  </Button>
                  <Button
                    size="small"
                    disabled={busyItem !== null}
                    onClick={() => void regenerate(row)}
                  >
                    Regenerate
                  </Button>
                  <Button
                    size="small"
                    variant="contained"
                    disabled={
                      busyItem !== null ||
                      !row.annotation_id ||
                      row.review_status !== "candidate"
                    }
                    onClick={() => void approve(row)}
                  >
                    Approve
                  </Button>
                </Stack>
              </Stack>
            </Stack>
          </Paper>
        ))
      )}
      {nextCursor && (
        <Button disabled={loadingMore} onClick={() => void loadMore()}>
          {loadingMore ? "Loading…" : "Load more captions"}
        </Button>
      )}
    </Stack>
  );
}
