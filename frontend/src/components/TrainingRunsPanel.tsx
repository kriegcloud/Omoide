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
  InputLabel,
  LinearProgress,
  MenuItem,
  Paper,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import StopIcon from "@mui/icons-material/Stop";
import {
  cancelTrainingRun,
  createTrainingRun,
  getTrainingHealth,
  getTrainingPresets,
  getTrainingSamples,
  trainingSampleImageUrl,
} from "../services/datasets";
import type { DatasetExport, TrainingHealth, TrainingPreset, TrainingRun, TrainingSample } from "../types";

interface TrainingRunsPanelProps {
  datasetId: number;
  exports: DatasetExport[];
  runs: TrainingRun[];
  onRunsChange: (runs: TrainingRun[]) => void;
  health: TrainingHealth | null;
  onHealthChange: (health: TrainingHealth) => void;
}

const activeStatuses = new Set<TrainingRun["status"]>(["requested", "running"]);

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
      </Stack>

      {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}

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
                      <Box key={step}>
                        <Typography variant="body2" fontWeight={700} sx={{ mb: 1 }}>Step {step.toLocaleString()}</Typography>
                        <Box sx={{ display: "grid", gridTemplateColumns: { xs: "repeat(2, minmax(0, 1fr))", sm: "repeat(3, minmax(0, 1fr))", lg: "repeat(4, minmax(0, 1fr))" }, gap: 1.5 }}>
                          {stepSamples.map((sample) => (
                            <Box
                              key={sample.id}
                              component="img"
                              src={trainingSampleImageUrl(run.id, sample.id)}
                              alt={`Training sample from step ${step}`}
                              loading="lazy"
                              sx={{ width: "100%", aspectRatio: "1 / 1", objectFit: "cover", borderRadius: 2, bgcolor: "action.hover" }}
                            />
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
