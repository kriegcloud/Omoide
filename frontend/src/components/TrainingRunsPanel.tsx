import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  IconButton,
  InputLabel,
  LinearProgress,
  Menu,
  MenuItem,
  Paper,
  Select,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import StopIcon from "@mui/icons-material/Stop";
import MoreVertIcon from "@mui/icons-material/MoreVert";
import {
  cancelTrainingRun,
  createTrainingRun,
  getDatasetRunLikeness,
  getTrainingHealth,
  getTrainingPresets,
  getTrainingRuns,
  getTrainingSamples,
  rescoreTrainingRun,
  trainingSampleImageUrl,
} from "../services/datasets";
import type { DatasetExport, RunLikeness, TrainingHealth, TrainingPreset, TrainingRun, TrainingSample } from "../types";
import LikenessSparkline from "./LikenessSparkline";

interface TrainingRunsPanelProps {
  datasetId: number;
  exports: DatasetExport[];
  runs: TrainingRun[];
  onRunsChange: (runs: TrainingRun[]) => void;
  health: TrainingHealth | null;
  onHealthChange: (health: TrainingHealth) => void;
}

const activeStatuses = new Set<TrainingRun["status"]>(["requested", "running"]);
const curveColors = ["#7c3aed", "#0891b2", "#dc2626", "#16a34a", "#ea580c", "#2563eb"];

function statusColor(status: TrainingRun["status"]): "default" | "info" | "success" | "error" | "warning" {
  if (status === "completed") return "success";
  if (status === "failed") return "error";
  if (status === "cancelled") return "warning";
  if (status === "running") return "info";
  return "default";
}

function elapsed(run: TrainingRun): string {
  const start = new Date(run.started_at ?? run.created_at).getTime();
  const end = run.finished_at ? new Date(run.finished_at).getTime() : Date.now();
  const seconds = Math.max(0, Math.floor((end - start) / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m ${seconds % 60}s`;
}

export default function TrainingRunsPanel({ datasetId, exports, runs, onRunsChange, health, onHealthChange }: TrainingRunsPanelProps) {
  const completedExports = useMemo(
    () => exports.filter((entry) => entry.status === "completed"),
    [exports],
  );
  const latestExportId = completedExports[0]?.id;
  const [dialogOpen, setDialogOpen] = useState(false);
  const [exportId, setExportId] = useState<number | "">(latestExportId ?? "");
  const [presets, setPresets] = useState<TrainingPreset[]>([]);
  const [baseModel, setBaseModel] = useState("");
  const [steps, setSteps] = useState(2000);
  const [learningRate, setLearningRate] = useState(0.0001);
  const [rank, setRank] = useState(16);
  const [prompts, setPrompts] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [samples, setSamples] = useState<Record<number, TrainingSample[]>>({});
  const [loadingSamples, setLoadingSamples] = useState<Set<number>>(new Set());
  const [likeness, setLikeness] = useState<Record<number, RunLikeness>>({});
  const [compareRuns, setCompareRuns] = useState(false);
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null);
  const [menuRunId, setMenuRunId] = useState<number | null>(null);

  useEffect(() => {
    void Promise.all([getTrainingHealth(), getTrainingPresets()])
      .then(([nextHealth, nextPresets]) => {
        onHealthChange(nextHealth);
        setPresets(nextPresets);
        setBaseModel(nextPresets.find((preset) => preset.is_default)?.id ?? nextPresets[0]?.id ?? "");
      })
      .catch((reason) => {
        setError(reason instanceof Error ? reason.message : "Could not load training configuration");
      });
  }, [onHealthChange]);

  useEffect(() => {
    const visibleActiveRuns = runs.filter((run) => expanded.has(run.id) && activeStatuses.has(run.status));
    if (visibleActiveRuns.length === 0) return;
    void Promise.all(visibleActiveRuns.map(async (run) => {
      const next = await getTrainingSamples(run.id);
      setSamples((current) => ({ ...current, [run.id]: next }));
    })).catch((reason) => {
      setError(reason instanceof Error ? reason.message : "Could not refresh training samples");
    });
  }, [expanded, runs]);

  useEffect(() => {
    void getDatasetRunLikeness(datasetId)
      .then((entries) => setLikeness(Object.fromEntries(entries.map((entry) => [entry.run_id, entry]))))
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Could not load likeness scores"));
  }, [datasetId, runs]);

  useEffect(() => {
    if (!Object.values(likeness).some((entry) => entry.pending > 0)) return;
    const timer = window.setInterval(() => {
      void getDatasetRunLikeness(datasetId).then(async (entries) => {
        setLikeness(Object.fromEntries(entries.map((entry) => [entry.run_id, entry])));
        const visible = entries.filter((entry) => expanded.has(entry.run_id));
        if (visible.length > 0) {
          const refreshed = await Promise.all(visible.map((entry) => getTrainingSamples(entry.run_id)));
          setSamples((current) => ({
            ...current,
            ...Object.fromEntries(visible.map((entry, index) => [entry.run_id, refreshed[index]])),
          }));
        }
        if (entries.every((entry) => entry.pending === 0)) {
          onRunsChange(await getTrainingRuns(datasetId));
        }
      }).catch((reason) => setError(reason instanceof Error ? reason.message : "Could not refresh likeness scores"));
    }, 5000);
    return () => window.clearInterval(timer);
  }, [datasetId, expanded, likeness, onRunsChange]);

  const openDialog = () => {
    setExportId(latestExportId ?? "");
    setBaseModel(presets.find((preset) => preset.is_default)?.id ?? presets[0]?.id ?? "");
    setError(null);
    setDialogOpen(true);
  };

  const toggleDetails = async (run: TrainingRun) => {
    if (expanded.has(run.id)) {
      setExpanded((current) => {
        const next = new Set(current);
        next.delete(run.id);
        return next;
      });
      return;
    }
    setExpanded((current) => new Set(current).add(run.id));
    if (samples[run.id]) return;
    setLoadingSamples((current) => new Set(current).add(run.id));
    try {
      const next = await getTrainingSamples(run.id);
      setSamples((current) => ({ ...current, [run.id]: next }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load training samples");
    } finally {
      setLoadingSamples((current) => {
        const next = new Set(current);
        next.delete(run.id);
        return next;
      });
    }
  };

  return (
    <Stack spacing={2.5}>
      {health && !health.launcher_ok && (
        <Alert severity="warning">
          Training launcher last seen {health.launcher_seen_at ? new Date(health.launcher_seen_at).toLocaleString() : "never"}.
          {" "}Enable it with <code>systemctl --user enable --now omoide-train.timer</code>; see <code>packaging/README-training.md</code>.
        </Alert>
      )}
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems={{ sm: "center" }}>
        <Button
          variant="contained"
          startIcon={<PlayArrowIcon />}
          disabled={completedExports.length === 0}
          onClick={openDialog}
        >
          Train LoRA…
        </Button>
        {completedExports.length === 0 && (
          <Typography variant="body2" color="text.secondary">
            Complete an export before starting a training run.
          </Typography>
        )}
        {runs.some((run) => run.status === "completed") && (
          <FormControlLabel
            sx={{ ml: { sm: "auto" } }}
            control={<Switch checked={compareRuns} onChange={(event) => setCompareRuns(event.target.checked)} />}
            label="Compare runs"
          />
        )}
      </Stack>

      {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}

      {compareRuns && (
        <Paper elevation={0} sx={{ p: 2, border: 1, borderColor: "divider", borderRadius: 3 }}>
          <Typography variant="subtitle2">Completed-run likeness</Typography>
          <Box sx={{ mt: 1, minHeight: 84 }}>
            <LikenessSparkline
              height={84}
              series={runs.filter((run) => run.status === "completed").map((run, index) => ({
                id: run.id,
                color: curveColors[index % curveColors.length],
                points: likeness[run.id]?.steps ?? [],
              }))}
              label="Comparison of likeness curves for completed training runs"
            />
          </Box>
          <Stack direction="row" gap={1.5} flexWrap="wrap" useFlexGap sx={{ mt: 1 }}>
            {runs.filter((run) => run.status === "completed").map((run, index) => (
              <Stack key={run.id} direction="row" spacing={0.75} alignItems="center">
                <Box sx={{ width: 10, height: 10, borderRadius: "50%", bgcolor: curveColors[index % curveColors.length] }} />
                <Typography variant="caption">
                  #{run.id} · {run.base_model} · rank {run.rank ?? "—"} · lr {run.lr == null ? "—" : run.lr.toPrecision(3)} · {run.steps.toLocaleString()} steps
                </Typography>
              </Stack>
            ))}
          </Stack>
        </Paper>
      )}

      {runs.length === 0 ? (
        <Box sx={{ py: 6, textAlign: "center" }}>
          <Typography variant="h6">No training runs yet</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.5 }}>
            Start a LoRA run from a completed ai-toolkit export.
          </Typography>
        </Box>
      ) : runs.map((run) => {
        const total = run.total_steps || run.steps;
        const progress = total > 0 ? Math.min(100, (run.current_step / total) * 100) : 0;
        const grouped = new Map<number, TrainingSample[]>();
        for (const sample of samples[run.id] ?? []) {
          grouped.set(sample.step, [...(grouped.get(sample.step) ?? []), sample]);
        }
        const isExpanded = expanded.has(run.id);
        const runLikeness = likeness[run.id];
        return (
          <Paper key={run.id} elevation={0} sx={{ border: 1, borderColor: "divider", borderRadius: 3, overflow: "hidden" }}>
            <Box sx={{ p: { xs: 2, md: 2.5 } }}>
              <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" gap={2}>
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                    <Typography fontWeight={700}>Run #{run.id}</Typography>
                    <Chip size="small" label={run.status} color={statusColor(run.status)} />
                    <Chip size="small" variant="outlined" label={run.base_model} />
                    <Typography variant="body2" color="text.secondary">
                      {new Date(run.created_at).toLocaleString()}
                    </Typography>
                  </Stack>
                  <Stack direction="row" spacing={2} sx={{ mt: 1 }} flexWrap="wrap" useFlexGap>
                    <Typography variant="body2" sx={{ fontVariantNumeric: "tabular-nums" }}>
                      {run.current_step.toLocaleString()} / {total.toLocaleString()} steps
                    </Typography>
                    <Typography variant="body2" color="text.secondary" sx={{ fontVariantNumeric: "tabular-nums" }}>
                      Loss {run.last_loss == null ? "—" : run.last_loss.toPrecision(5)}
                    </Typography>
                    <Typography variant="body2" color="text.secondary" sx={{ fontVariantNumeric: "tabular-nums" }}>
                      Elapsed {elapsed(run)}
                    </Typography>
                  </Stack>
                  <LinearProgress
                    variant="determinate"
                    value={progress}
                    aria-label={`Training progress: ${run.current_step} of ${total} steps`}
                    sx={{ mt: 1.25, height: 7, borderRadius: 1 }}
                  />
                  {runLikeness && (
                    <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems={{ sm: "center" }} sx={{ mt: 1.25 }}>
                      <Box sx={{ width: { xs: "100%", sm: 180 } }}>
                        <LikenessSparkline series={[{ id: run.id, color: curveColors[0], points: runLikeness.steps }]} />
                      </Box>
                      <Typography variant="body2" color="text.secondary" sx={{ fontVariantNumeric: "tabular-nums" }}>
                        {runLikeness.best_step == null
                          ? runLikeness.pending > 0 ? `${runLikeness.pending} pending` : "No likeness scores"
                          : `Best step ${runLikeness.best_step.toLocaleString()} · ${runLikeness.best?.toFixed(3)}`}
                      </Typography>
                    </Stack>
                  )}
                  {run.error && <Alert severity="error" sx={{ mt: 1.5 }}>{run.error}</Alert>}
                </Box>
                <Stack direction="row" spacing={1} alignItems="flex-start">
                  {activeStatuses.has(run.status) && (
                    <Button
                      color="error"
                      startIcon={<StopIcon />}
                      onClick={async () => {
                        try {
                          const updated = await cancelTrainingRun(run.id);
                          onRunsChange(runs.map((entry) => entry.id === run.id ? updated : entry));
                        } catch (reason) {
                          setError(reason instanceof Error ? reason.message : "Could not cancel training");
                        }
                      }}
                    >
                      Cancel
                    </Button>
                  )}
                  <Button
                    endIcon={<ExpandMoreIcon sx={{ transform: isExpanded ? "rotate(180deg)" : "none", transition: "transform 180ms ease-out" }} />}
                    aria-expanded={isExpanded}
                    aria-controls={`run-${run.id}-details`}
                    onClick={() => void toggleDetails(run)}
                  >
                    {isExpanded ? "Hide details" : "View details"}
                  </Button>
                  <IconButton
                    aria-label={`Actions for run ${run.id}`}
                    onClick={(event) => {
                      setMenuAnchor(event.currentTarget);
                      setMenuRunId(run.id);
                    }}
                  >
                    <MoreVertIcon />
                  </IconButton>
                </Stack>
              </Stack>
            </Box>

            {isExpanded && (
              <Box id={`run-${run.id}-details`} sx={{ px: { xs: 2, md: 2.5 }, pb: 2.5, borderTop: 1, borderColor: "divider", pt: 2 }}>
                {run.checkpoints.length > 0 && (
                  <Box sx={{ mb: 2.5 }}>
                    <Typography variant="subtitle2" gutterBottom>Checkpoints</Typography>
                    <Stack spacing={0.5}>
                      {run.checkpoints.map((path) => (
                        <Typography key={path} component="code" variant="caption" sx={{ overflowWrap: "anywhere" }}>{path}</Typography>
                      ))}
                    </Stack>
                  </Box>
                )}
                <Typography variant="subtitle2" gutterBottom>Training samples</Typography>
                {loadingSamples.has(run.id) ? <CircularProgress size={24} /> : grouped.size === 0 ? (
                  <Typography variant="body2" color="text.secondary">No sample images have been generated yet.</Typography>
                ) : (
                  <Stack spacing={2.5}>
                    {[...grouped.entries()].sort(([left], [right]) => right - left).map(([step, stepSamples]) => (
                      <Box
                        key={step}
                        sx={step === run.likeness_best_step ? { p: 1.5, mx: -1.5, border: 2, borderColor: "success.main", borderRadius: 2 } : undefined}
                      >
                        <Typography variant="body2" fontWeight={700} sx={{ mb: 1 }}>Step {step.toLocaleString()}</Typography>
                        <Box sx={{ display: "grid", gridTemplateColumns: { xs: "repeat(2, minmax(0, 1fr))", sm: "repeat(3, minmax(0, 1fr))", lg: "repeat(4, minmax(0, 1fr))" }, gap: 1.5 }}>
                          {stepSamples.map((sample) => (
                            <Box key={sample.id} sx={{ position: "relative" }}>
                              <Box
                                component="img"
                                src={trainingSampleImageUrl(run.id, sample.id)}
                                alt={`Training sample from step ${step}`}
                                loading="lazy"
                                sx={{ display: "block", width: "100%", aspectRatio: "1 / 1", objectFit: "cover", borderRadius: 2, bgcolor: "action.hover" }}
                              />
                              <Chip
                                size="small"
                                label={sample.likeness == null ? sample.face_count === 0 ? "No face" : "Unscored" : sample.likeness.toFixed(3)}
                                color={sample.likeness == null ? "default" : "success"}
                                sx={{ position: "absolute", left: 6, bottom: 6, bgcolor: "background.paper" }}
                              />
                            </Box>
                          ))}
                        </Box>
                      </Box>
                    ))}
                  </Stack>
                )}
              </Box>
            )}
          </Paper>
        );
      })}

      <Menu anchorEl={menuAnchor} open={menuAnchor !== null} onClose={() => setMenuAnchor(null)}>
        <MenuItem
          onClick={async () => {
            const runId = menuRunId;
            setMenuAnchor(null);
            if (runId === null) return;
            try {
              const result = await rescoreTrainingRun(runId);
              setLikeness((current) => ({
                ...current,
                [runId]: { run_id: runId, steps: [], best_step: null, best: null, scored: 0, pending: result.queued },
              }));
              onRunsChange(runs.map((run) => run.id === runId ? { ...run, likeness_best_step: null, likeness_best: null } : run));
              setSamples((current) => {
                const next = { ...current };
                delete next[runId];
                return next;
              });
            } catch (reason) {
              setError(reason instanceof Error ? reason.message : "Could not queue likeness rescore");
            }
          }}
        >
          Rescore likeness
        </MenuItem>
      </Menu>

      <Dialog open={dialogOpen} onClose={() => !submitting && setDialogOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>Train LoRA</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <FormControl fullWidth>
              <InputLabel>Dataset export</InputLabel>
              <Select label="Dataset export" value={exportId} onChange={(event) => setExportId(Number(event.target.value))}>
                {completedExports.map((entry) => (
                  <MenuItem key={entry.id} value={entry.id}>
                    {new Date(entry.created_at).toLocaleString()} · {entry.item_count} items · {entry.layout}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControl fullWidth>
              <InputLabel>Base model</InputLabel>
              <Select label="Base model" value={baseModel} onChange={(event) => setBaseModel(event.target.value)}>
                {presets.map((preset) => (
                  <MenuItem key={preset.id} value={preset.id} disabled={!preset.available}>
                    <Box>
                      <Typography variant="body2">{preset.label}</Typography>
                      <Typography variant="caption" color="text.secondary">
                        {preset.available ? preset.description : "Needs a Hugging Face token on the host"}
                      </Typography>
                    </Box>
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
              <TextField fullWidth type="number" label="Steps" value={steps} onChange={(event) => setSteps(Number(event.target.value))} inputProps={{ min: 1, step: 100 }} />
              <TextField fullWidth type="number" label="Learning rate" value={learningRate} onChange={(event) => setLearningRate(Number(event.target.value))} inputProps={{ min: 0, step: 0.00001 }} />
              <TextField fullWidth type="number" label="Rank" value={rank} onChange={(event) => setRank(Number(event.target.value))} inputProps={{ min: 1, step: 1 }} />
            </Stack>
            <TextField
              multiline
              minRows={4}
              label="Sample prompts"
              value={prompts}
              onChange={(event) => setPrompts(event.target.value)}
              helperText="One prompt per line. Leave blank to use the export defaults."
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button disabled={submitting} onClick={() => setDialogOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            startIcon={submitting ? <CircularProgress size={18} color="inherit" /> : <PlayArrowIcon />}
            disabled={submitting || exportId === "" || baseModel === "" || steps <= 0 || learningRate <= 0 || rank <= 0}
            onClick={async () => {
              if (exportId === "") return;
              setSubmitting(true);
              setError(null);
              try {
                const samplePrompts = prompts.split("\n").map((prompt) => prompt.trim()).filter(Boolean);
                const created = await createTrainingRun(datasetId, {
                  export_id: exportId,
                  base_model: baseModel,
                  steps,
                  lr: learningRate,
                  rank,
                  ...(samplePrompts.length ? { sample_prompts: samplePrompts } : {}),
                });
                onRunsChange([created, ...runs]);
                setDialogOpen(false);
              } catch (reason) {
                setError(reason instanceof Error ? reason.message : "Could not start training");
              } finally {
                setSubmitting(false);
              }
            }}
          >
            Start training
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
