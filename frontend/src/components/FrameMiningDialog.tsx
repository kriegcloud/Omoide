import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Paper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { getFrameMiningCandidates, mineDatasetFrames } from "../services/datasets";
import type { FrameCandidate, FrameMiningVideo } from "../types";

interface FrameMiningDialogProps {
  open: boolean;
  datasetId: number;
  onClose: () => void;
  onStarted: () => void;
}

const formatDuration = (seconds?: number | null) => {
  if (seconds == null) return "Unknown duration";
  const rounded = Math.max(0, Math.round(seconds));
  return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, "0")}`;
};

export default function FrameMiningDialog({
  open,
  datasetId,
  onClose,
  onStarted,
}: FrameMiningDialogProps) {
  const [videos, setVideos] = useState<FrameMiningVideo[]>([]);
  const [selectedVideos, setSelectedVideos] = useState<Set<number>>(new Set());
  const [activeVideo, setActiveVideo] = useState<number | null>(null);
  const [previews, setPreviews] = useState<Record<number, FrameCandidate[]>>({});
  const [selectedFrames, setSelectedFrames] = useState<Record<number, Set<number>>>({});
  const [maxPerVideo, setMaxPerVideo] = useState(12);
  const [minFacePx, setMinFacePx] = useState(160);
  const [fps, setFps] = useState(2);
  const [loading, setLoading] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setPreviews({});
    setSelectedFrames({});
    void getFrameMiningCandidates(datasetId)
      .then((result) => {
        if (cancelled) return;
        setVideos(result.videos);
        const defaults = result.videos.filter((video) => video.already_mined_count === 0);
        setSelectedVideos(new Set(defaults.map((video) => video.media_id)));
        setActiveVideo((defaults[0] ?? result.videos[0])?.media_id ?? null);
      })
      .catch((reason) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Could not load videos");
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [datasetId, open]);

  const activeCandidates = useMemo(
    () => activeVideo == null ? [] : previews[activeVideo] ?? [],
    [activeVideo, previews],
  );

  const preview = async () => {
    if (activeVideo == null) return;
    setPreviewing(true);
    setError(null);
    try {
      const result = await getFrameMiningCandidates(datasetId, {
        video_media_id: activeVideo,
        min_face_px: minFacePx,
        fps,
        max_candidates: 48,
      });
      setPreviews((current) => ({ ...current, [activeVideo]: result.candidates }));
      setSelectedFrames((current) => ({
        ...current,
        [activeVideo]: new Set(result.candidates.slice(0, maxPerVideo).map((candidate) => candidate.timestamp_ms)),
      }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not preview frames");
    } finally {
      setPreviewing(false);
    }
  };

  const start = async () => {
    setStarting(true);
    setError(null);
    try {
      const selectedTimestamps = Object.fromEntries(
        Object.entries(selectedFrames)
          .filter(([mediaId]) => selectedVideos.has(Number(mediaId)))
          .map(([mediaId, values]) => [Number(mediaId), [...values]]),
      );
      await mineDatasetFrames(datasetId, {
        video_media_ids: [...selectedVideos],
        max_per_video: maxPerVideo,
        min_face_px: minFacePx,
        fps,
        selected_timestamps_ms: selectedTimestamps,
      });
      onStarted();
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not start frame mining");
    } finally {
      setStarting(false);
    }
  };

  return (
    <Dialog open={open} onClose={starting ? undefined : onClose} fullWidth maxWidth="lg">
      <DialogTitle>Mine video frames</DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        {loading ? <Box minHeight={220} display="grid" sx={{ placeItems: "center" }}><CircularProgress /></Box> : (
          <Stack spacing={3}>
            <Box>
              <Typography variant="subtitle1" fontWeight={700} gutterBottom>Subject videos</Typography>
              {videos.length === 0 ? <Typography color="text.secondary">No videos with detected subject faces.</Typography> : (
                <Stack spacing={1}>
                  {videos.map((video) => (
                    <Paper key={video.media_id} variant="outlined" sx={{ px: 1.5, py: 1 }}>
                      <Stack direction="row" alignItems="center" gap={1}>
                        <FormControlLabel
                          sx={{ flex: 1, m: 0 }}
                          control={<Checkbox checked={selectedVideos.has(video.media_id)} onChange={(_, checked) => setSelectedVideos((current) => { const next = new Set(current); if (checked) next.add(video.media_id); else next.delete(video.media_id); return next; })} />}
                          label={<Box><Typography variant="body2" fontWeight={600}>{video.filename}</Typography><Typography variant="caption" color="text.secondary">{formatDuration(video.duration)} · {video.detected_face_count} detected faces · {video.already_mined_count} already mined</Typography></Box>}
                        />
                        <Button size="small" variant={activeVideo === video.media_id ? "contained" : "text"} onClick={() => setActiveVideo(video.media_id)}>Preview</Button>
                      </Stack>
                    </Paper>
                  ))}
                </Stack>
              )}
            </Box>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
              <TextField type="number" label="Maximum per video" value={maxPerVideo} onChange={(event) => setMaxPerVideo(Math.max(1, Number(event.target.value)))} inputProps={{ min: 1, max: 100 }} />
              <TextField type="number" label="Minimum face size (px)" value={minFacePx} onChange={(event) => setMinFacePx(Math.max(1, Number(event.target.value)))} inputProps={{ min: 1 }} />
              <TextField type="number" label="Samples per second" value={fps} onChange={(event) => setFps(Math.max(0.1, Number(event.target.value)))} inputProps={{ min: 0.1, max: 30, step: 0.5 }} />
              <Button variant="outlined" disabled={activeVideo == null || previewing} onClick={() => void preview()}>{previewing ? "Decoding…" : "Load preview"}</Button>
            </Stack>
            {activeVideo != null && previews[activeVideo] && (
              <Box>
                <Typography variant="subtitle1" fontWeight={700} gutterBottom>Candidate frames</Typography>
                {activeCandidates.length === 0 ? <Typography color="text.secondary">No matching frames met the face-size and identity thresholds.</Typography> : (
                  <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "repeat(2, 1fr)", sm: "repeat(3, 1fr)", md: "repeat(4, 1fr)", lg: "repeat(6, 1fr)" } }}>
                    {activeCandidates.map((candidate) => {
                      const checked = selectedFrames[activeVideo]?.has(candidate.timestamp_ms) ?? false;
                      return <Paper key={candidate.timestamp_ms} variant="outlined" sx={{ overflow: "hidden", position: "relative" }}>
                        <Checkbox checked={checked} onChange={(_, nextChecked) => setSelectedFrames((current) => { const next = new Set(current[activeVideo] ?? []); if (nextChecked) next.add(candidate.timestamp_ms); else next.delete(candidate.timestamp_ms); return { ...current, [activeVideo]: next }; })} sx={{ position: "absolute", zIndex: 1, top: 2, left: 2, bgcolor: "background.paper", borderRadius: 1, p: 0.5 }} />
                        <Box component="img" src={candidate.preview_data_url ?? undefined} alt={`Frame at ${(candidate.timestamp_ms / 1000).toFixed(2)} seconds`} sx={{ width: "100%", aspectRatio: "16 / 10", objectFit: "cover", display: "block", bgcolor: "action.hover" }} />
                        <Box p={1}><Typography variant="caption" display="block">{(candidate.timestamp_ms / 1000).toFixed(2)}s · likeness {candidate.likeness.toFixed(2)}</Typography><Typography variant="caption" color="text.secondary">sharpness {candidate.sharpness.toFixed(0)} · face {candidate.face_size.toFixed(0)}px</Typography></Box>
                      </Paper>;
                    })}
                  </Box>
                )}
              </Box>
            )}
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={starting}>Cancel</Button>
        <Button variant="contained" disabled={starting || selectedVideos.size === 0} onClick={() => void start()}>{starting ? "Starting…" : `Mine ${selectedVideos.size} video${selectedVideos.size === 1 ? "" : "s"}`}</Button>
      </DialogActions>
    </Dialog>
  );
}
