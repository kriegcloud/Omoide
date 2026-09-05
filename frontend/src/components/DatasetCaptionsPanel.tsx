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

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await getDatasetCaptions(datasetId, filter);
      setRows(page.items);
      setNextCursor(page.next_cursor ?? null);
      setDrafts(
        Object.fromEntries(page.items.map((row) => [row.item_id, row.caption])),
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Failed to load captions",
      );
    } finally {
      setLoading(false);
    }
  }, [datasetId, filter]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!task || !["pending", "running"].includes(task.status)) return;
    const timer = window.setInterval(() => {
      void getTask(task.id)
        .then((nextTask) => {
          setTask(nextTask);
          if (!["pending", "running"].includes(nextTask.status)) void load();
        })
        .catch((reason) =>
          setError(
            reason instanceof Error
              ? reason.message
              : "Failed to refresh caption generation",
          ),
        );
    }, 1500);
    return () => window.clearInterval(timer);
  }, [task, load]);

  const save = async (row: DatasetCaption) => {
    setBusyItem(row.item_id);
    try {
      await updateDatasetCaption(
        datasetId,
        row.item_id,
        drafts[row.item_id] ?? row.caption,
      );
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to save caption");
    } finally {
      setBusyItem(null);
    }
  };

  const approve = async (row: DatasetCaption) => {
    if (!row.annotation_id || row.review_status !== "candidate") return;
    setBusyItem(row.item_id);
    try {
      await approveAnnotation(row.annotation_id);
      await getMediaAnnotations(row.media_id);
      await markDatasetCaptionReviewed(datasetId, row.item_id);
      await load();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Failed to approve caption",
      );
    } finally {
      setBusyItem(null);
    }
  };

  const regenerate = async (row: DatasetCaption) => {
    setBusyItem(row.item_id);
    try {
      const attempt = await startAnnotation(row.media_id, "caption");
      for (;;) {
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
        const state = await getMediaAnnotations(row.media_id);
        const current = state.attempts.find((entry) => entry.id === attempt.id);
        if (current && TERMINAL_ATTEMPT_STATUSES.has(current.status)) break;
      }
      await load();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Failed to regenerate caption",
      );
    } finally {
      setBusyItem(null);
    }
  };

  const generateMissing = async () => {
    try {
      setError(null);
      setTask(await generateDatasetCaptions(datasetId, true));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Failed to generate captions",
      );
    }
  };

  const loadMore = async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await getDatasetCaptions(datasetId, filter, nextCursor);
      setRows((current) => [...current, ...page.items]);
      setDrafts((current) => ({
        ...current,
        ...Object.fromEntries(page.items.map((row) => [row.item_id, row.caption])),
      }));
      setNextCursor(page.next_cursor ?? null);
    } finally {
      setLoadingMore(false);
    }
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
          disabled={taskActive}
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
                src={`${API}/thumbnails/${encodeFilePath(row.media.thumbnail_path)}`}
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
                    disabled={busyItem === row.item_id}
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
