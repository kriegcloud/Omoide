import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Button,
  Chip,
  LinearProgress,
  Stack,
  Typography,
} from "@mui/material";
import {
  useTaskCompletionVersion,
  useTaskEvents,
} from "../TaskEventsContext";
import { getOrphanFaceCount } from "../services/face";
import { getRecentTasks, startTask } from "../services/taskActions";
import { Task } from "../types";
import {
  formatRelativeTime,
  formatTaskDuration,
  formatTaskStep,
} from "../utils/taskFormat";

interface ClusteringStatusStripProps {
  onRefresh: () => void;
  onShowUnassigned: () => void;
}

export default function ClusteringStatusStrip({
  onRefresh,
  onShowUnassigned,
}: ClusteringStatusStripProps) {
  const { activeTasks, recentTasks, lastFinishedTask, forceRefresh } =
    useTaskEvents();
  const completionVersion = useTaskCompletionVersion([
    "cluster_persons",
    "process_media",
    "scan",
  ]);
  const [fallbackTask, setFallbackTask] = useState<Task | null>(null);
  const [orphanCount, setOrphanCount] = useState<number | null>(null);
  const [countUnavailable, setCountUnavailable] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const fallbackRequestedRef = useRef(false);
  const lastFinishedNonceRef = useRef(lastFinishedTask?.nonce ?? null);

  const activeCluster = useMemo(
    () =>
      activeTasks.find(
        (task) =>
          task.task_type === "cluster_persons" &&
          (task.status === "running" || task.status === "pending")
      ),
    [activeTasks]
  );
  const recentCluster = useMemo(
    () => recentTasks.find((task) => task.task_type === "cluster_persons"),
    [recentTasks]
  );
  const lastCluster = recentCluster ?? fallbackTask;

  const loadOrphanCount = useCallback(async () => {
    try {
      const count = await getOrphanFaceCount();
      setOrphanCount(count);
      setCountUnavailable(false);
    } catch (error) {
      console.error("Failed to load unassigned face count", error);
      setCountUnavailable(true);
    }
  }, []);

  useEffect(() => {
    void loadOrphanCount();
  }, [completionVersion, loadOrphanCount]);

  useEffect(() => {
    if (recentCluster || fallbackRequestedRef.current) return;
    fallbackRequestedRef.current = true;
    getRecentTasks(1, "cluster_persons")
      .then(([task]) => setFallbackTask(task ?? null))
      .catch((error) => {
        console.error("Failed to load the last clustering task", error);
      });
  }, [recentCluster]);

  useEffect(() => {
    if (
      !lastFinishedTask ||
      lastFinishedTask.nonce === lastFinishedNonceRef.current
    ) {
      return;
    }
    lastFinishedNonceRef.current = lastFinishedTask.nonce;
    if (lastFinishedTask.task.task_type !== "cluster_persons") return;

    setFallbackTask(lastFinishedTask.task);
    void loadOrphanCount();
    onRefresh();
  }, [lastFinishedTask, loadOrphanCount, onRefresh]);

  const handleStart = async () => {
    setIsStarting(true);
    setStartError(null);
    try {
      await startTask("cluster_persons");
      await forceRefresh();
    } catch (error) {
      console.error("Failed to start face clustering", error);
      setStartError("Could not start clustering. Try again.");
    } finally {
      setIsStarting(false);
    }
  };

  const progressValue =
    activeCluster && activeCluster.total > 0
      ? Math.min(100, (activeCluster.processed / activeCluster.total) * 100)
      : undefined;
  const duration = lastCluster
    ? formatTaskDuration(lastCluster.duration_seconds)
    : null;

  return (
    <Box
      sx={{
        mb: 3,
        bgcolor: "background.paper",
        borderRadius: 2,
        px: 1.5,
        pt: 1.5,
        pb: activeCluster ? 1 : 1.5,
      }}
    >
      <Stack
        direction={{ xs: "column", sm: "row" }}
        spacing={{ xs: 1.25, sm: 2 }}
        alignItems={{ xs: "stretch", sm: "center" }}
      >
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Typography variant="subtitle2" fontWeight={700}>
            Face clustering
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {startError
              ? startError
              : activeCluster
                ? `Running · ${formatTaskStep(activeCluster.current_step)} · ${activeCluster.processed}/${activeCluster.total}`
                : lastCluster
                  ? `Last run ${formatRelativeTime(lastCluster)} · ${lastCluster.summary || "Finished"}${duration && duration !== "—" ? ` · ${duration}` : ""}`
                  : "Never run"}
          </Typography>
        </Box>

        <Chip
          size="small"
          variant="outlined"
          clickable
          onClick={onShowUnassigned}
          label={
            countUnavailable
              ? "Count unavailable"
              : orphanCount === null
                ? "Loading unassigned faces"
                : `${orphanCount} unassigned faces`
          }
        />

        <Button
          size="small"
          variant="outlined"
          onClick={() => void handleStart()}
          disabled={Boolean(activeCluster) || isStarting}
          sx={{ flexShrink: 0 }}
        >
          {isStarting ? "Starting…" : "Cluster now"}
        </Button>
      </Stack>

      {activeCluster && (
        <LinearProgress
          variant={progressValue === undefined ? "indeterminate" : "determinate"}
          value={progressValue}
          sx={{ mt: 1, mx: -1.5, mb: -1, height: 2 }}
        />
      )}
    </Box>
  );
}
