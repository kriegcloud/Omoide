import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  Menu,
  MenuItem,
  Paper,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { Link as RouterLink, useParams, useSearchParams } from "react-router-dom";
import BatchCropDialog from "../components/BatchCropDialog";
import config, { API } from "../config";
import {
  getDataset,
  getDatasetTriage,
  reviewDatasetItem,
  updateDatasetItem,
} from "../services/datasets";
import { startRepair } from "../services/repairs";
import type {
  DatasetItem,
  DatasetTriageEntry,
  DatasetTriageFilter,
  MediaPreview,
  RepairProfile,
} from "../types";

interface HistoryEntry {
  itemId: number;
  index: number;
  excluded: boolean;
  excluded_reason: "duplicate" | "burst" | "manual" | "quality" | null;
  reviewed_at: string | null;
  weight: number;
}

const FILTERS: DatasetTriageFilter[] = ["all", "findings", "excluded"];
const HOTKEYS = [
  ["K", "Keep and review"],
  ["X", "Exclude and review"],
  ["C", "Crop suggestion"],
  ["E", "Edit caption"],
  ["R", "Repair"],
  ["1 / 2 / 3", "Weight 0.5 / 1 / 1.5"],
  ["← / →", "Previous / next"],
  ["U", "Undo last action"],
  ["?", "Help"],
] as const;

const displayNumber = (value: number | null | undefined, digits = 2) =>
  value == null ? "—" : value.toFixed(digits);

export default function DatasetTriagePage() {
  const { id } = useParams();
  const datasetId = Number(id);
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedFilter = searchParams.get("filter") as DatasetTriageFilter | null;
  const filter = requestedFilter && FILTERS.includes(requestedFilter)
    ? requestedFilter
    : "all";
  const [entries, setEntries] = useState<DatasetTriageEntry[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [reviewedCount, setReviewedCount] = useState(0);
  const [totalCount, setTotalCount] = useState(0);
  const [personId, setPersonId] = useState<number | null>(null);
  const [caption, setCaption] = useState("");
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [cropOpen, setCropOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [repairAnchor, setRepairAnchor] = useState<HTMLElement | null>(null);
  const [imageSize, setImageSize] = useState({ width: 0, height: 0 });
  const imagePanelRef = useRef<HTMLDivElement>(null);
  const captionRef = useRef<HTMLInputElement>(null);
  const repairButtonRef = useRef<HTMLButtonElement>(null);
  const current = entries[currentIndex] ?? null;

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    setEntries([]);
    setCurrentIndex(0);
    setHistory([]);
    void Promise.all([
      getDatasetTriage(datasetId, filter),
      getDataset(datasetId),
    ])
      .then(([page, dataset]) => {
        if (!active) return;
        setEntries(page.items);
        setNextCursor(page.next_cursor ?? null);
        setReviewedCount(page.reviewed_count);
        setTotalCount(page.total_count);
        setPersonId(dataset.person_id ?? null);
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : "Failed to load triage queue");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [datasetId, filter]);

  useEffect(() => {
    setCaption(current?.caption ?? "");
  }, [current?.item.id, current?.caption]);

  useEffect(() => {
    const panel = imagePanelRef.current;
    if (!panel || !current?.media.width || !current.media.height) {
      setImageSize({ width: 0, height: 0 });
      return;
    }
    const resize = () => {
      const bounds = panel.getBoundingClientRect();
      const scale = Math.min(
        bounds.width / current.media.width!,
        bounds.height / current.media.height!,
      );
      setImageSize({
        width: Math.max(0, current.media.width! * scale),
        height: Math.max(0, current.media.height! * scale),
      });
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(panel);
    return () => observer.disconnect();
  }, [current?.item.id, current?.media.width, current?.media.height]);

  useEffect(() => {
    entries.slice(currentIndex + 1, currentIndex + 4).forEach((entry) => {
      const image = new Image();
      image.src = `${API}${entry.media.original_url}`;
    });
  }, [entries, currentIndex]);

  const replaceEntry = useCallback((itemId: number, patch: Partial<DatasetTriageEntry["item"]> & {
    caption?: string;
    effective_caption?: string | null;
  }) => {
    setEntries((rows) => rows.map((entry) => entry.item.id === itemId ? {
      ...entry,
      ...(patch.caption !== undefined ? { caption: patch.caption } : {}),
      ...(patch.effective_caption !== undefined ? { effective_caption: patch.effective_caption } : {}),
      item: { ...entry.item, ...patch },
    } : entry));
  }, []);

  const remember = useCallback((entry: DatasetTriageEntry, index: number) => {
    setHistory((rows) => [...rows, {
      itemId: entry.item.id,
      index,
      excluded: entry.item.excluded,
      excluded_reason: entry.item.excluded_reason ?? null,
      reviewed_at: entry.item.reviewed_at ?? null,
      weight: entry.item.weight,
    }]);
  }, []);

  const advance = useCallback(() => {
    setCurrentIndex((index) => Math.min(
      index + 1,
      Math.max(0, nextCursor ? entries.length : entries.length - 1),
    ));
  }, [entries.length, nextCursor]);

  const keep = useCallback(async () => {
    if (!current || busy) return;
    setBusy(true);
    setError(null);
    remember(current, currentIndex);
    try {
      const updated = await reviewDatasetItem(datasetId, current.item.id);
      replaceEntry(current.item.id, { reviewed_at: updated.reviewed_at ?? null });
      if (!current.item.reviewed_at && updated.reviewed_at) setReviewedCount((count) => count + 1);
      advance();
    } catch (reason) {
      setHistory((rows) => rows.slice(0, -1));
      setError(reason instanceof Error ? reason.message : "Failed to review item");
    } finally {
      setBusy(false);
    }
  }, [advance, busy, current, currentIndex, datasetId, remember, replaceEntry]);

  const exclude = useCallback(async () => {
    if (!current || busy) return;
    setBusy(true);
    setError(null);
    remember(current, currentIndex);
    const reviewedAt = current.item.reviewed_at ?? new Date().toISOString();
    try {
      const updated = await updateDatasetItem(datasetId, current.item.id, {
        excluded: true,
        excluded_reason: "manual",
        reviewed_at: reviewedAt,
      });
      replaceEntry(current.item.id, {
        excluded: updated.excluded,
        excluded_reason: updated.excluded_reason ?? "manual",
        reviewed_at: updated.reviewed_at ?? reviewedAt,
      });
      if (!current.item.reviewed_at) setReviewedCount((count) => count + 1);
      advance();
    } catch (reason) {
      setHistory((rows) => rows.slice(0, -1));
      setError(reason instanceof Error ? reason.message : "Failed to exclude item");
    } finally {
      setBusy(false);
    }
  }, [advance, busy, current, currentIndex, datasetId, remember, replaceEntry]);

  const setWeight = useCallback(async (weight: number) => {
    if (!current || busy || current.item.weight === weight) return;
    setBusy(true);
    setError(null);
    remember(current, currentIndex);
    try {
      const updated = await updateDatasetItem(datasetId, current.item.id, { weight });
      replaceEntry(current.item.id, { weight: updated.weight });
    } catch (reason) {
      setHistory((rows) => rows.slice(0, -1));
      setError(reason instanceof Error ? reason.message : "Failed to set weight");
    } finally {
      setBusy(false);
    }
  }, [busy, current, currentIndex, datasetId, remember, replaceEntry]);

  const undo = useCallback(async () => {
    const previous = history.at(-1);
    if (!previous || busy) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await updateDatasetItem(datasetId, previous.itemId, {
        excluded: previous.excluded,
        excluded_reason: previous.excluded_reason,
        reviewed_at: previous.reviewed_at,
        weight: previous.weight,
      });
      const existing = entries.find((entry) => entry.item.id === previous.itemId);
      if (existing?.item.reviewed_at && !previous.reviewed_at) setReviewedCount((count) => Math.max(0, count - 1));
      if (!existing?.item.reviewed_at && previous.reviewed_at) setReviewedCount((count) => count + 1);
      replaceEntry(previous.itemId, {
        excluded: updated.excluded,
        excluded_reason: updated.excluded_reason ?? null,
        reviewed_at: updated.reviewed_at ?? null,
        weight: updated.weight,
      });
      setCurrentIndex(previous.index);
      setHistory((rows) => rows.slice(0, -1));
      setNotice("Last action undone");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to undo action");
    } finally {
      setBusy(false);
    }
  }, [busy, datasetId, entries, history, replaceEntry]);

  const loadMore = useCallback(async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await getDatasetTriage(datasetId, filter, nextCursor);
      setEntries((rows) => {
        const seen = new Set(rows.map((entry) => entry.item.id));
        return [...rows, ...page.items.filter((entry) => !seen.has(entry.item.id))];
      });
      setNextCursor(page.next_cursor ?? null);
      setReviewedCount(page.reviewed_count);
      setTotalCount(page.total_count);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to load more items");
    } finally {
      setLoadingMore(false);
    }
  }, [datasetId, filter, loadingMore, nextCursor]);

  useEffect(() => {
    if (nextCursor && currentIndex >= entries.length - 3) void loadMore();
  }, [currentIndex, entries.length, loadMore, nextCursor]);

  const move = useCallback((direction: -1 | 1) => {
    if (direction < 0) {
      setCurrentIndex((index) => Math.max(0, index - 1));
      return;
    }
    if (currentIndex + 1 < entries.length) {
      setCurrentIndex((index) => index + 1);
    } else if (nextCursor) {
      void loadMore().then(() => setCurrentIndex((index) => index + 1));
    }
  }, [currentIndex, entries.length, loadMore, nextCursor]);

  const saveCaption = useCallback(async () => {
    if (!current) return;
    const next = caption.trim();
    if (next === current.caption.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await updateDatasetItem(datasetId, current.item.id, {
        caption_override: next || null,
      });
      replaceEntry(current.item.id, {
        caption: next,
        caption_override: updated.caption_override ?? null,
        effective_caption: updated.effective_caption ?? null,
      });
      setNotice("Caption saved");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to save caption");
    } finally {
      setBusy(false);
    }
  }, [caption, current, datasetId, replaceEntry]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      const editing = target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable;
      if (editing) {
        if (event.key === "Escape") target.blur();
        return;
      }
      if (event.key === "?") {
        event.preventDefault();
        setHelpOpen((open) => !open);
        return;
      }
      if (cropOpen || repairAnchor || helpOpen) return;
      const key = event.key.toLowerCase();
      if (["k", "x", "c", "e", "r", "1", "2", "3", "arrowleft", "arrowright", "u", "?"].includes(key)) {
        event.preventDefault();
      }
      if (key === "k") void keep();
      else if (key === "x") void exclude();
      else if (key === "c" && current?.face_crop_suggestion) setCropOpen(true);
      else if (key === "e") captionRef.current?.focus();
      else if (key === "r" && repairButtonRef.current) setRepairAnchor(repairButtonRef.current);
      else if (key === "1") void setWeight(0.5);
      else if (key === "2") void setWeight(1);
      else if (key === "3") void setWeight(1.5);
      else if (key === "arrowleft") move(-1);
      else if (key === "arrowright") move(1);
      else if (key === "u") void undo();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [cropOpen, current, exclude, helpOpen, keep, move, repairAnchor, setWeight, undo]);

  const beginRepair = async (profile: RepairProfile) => {
    if (!current) return;
    setRepairAnchor(null);
    setBusy(true);
    try {
      await startRepair(current.media.id, profile, personId ?? undefined);
      setNotice("Repair started");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to start repair");
    } finally {
      setBusy(false);
    }
  };

  const cropItem = useMemo<DatasetItem[]>(() => current ? [{
    id: current.item.id,
    dataset_id: datasetId,
    media_id: current.media.id,
    position: current.item.position,
    caption_override: current.item.caption_override,
    weight: current.item.weight,
    excluded: current.item.excluded,
    excluded_reason: current.item.excluded_reason,
    reviewed_at: current.item.reviewed_at,
    created_at: "",
    media: {
      id: current.media.id,
      filename: current.media.filename,
      path: current.media.path,
      size: 0,
      thumbnail_path: current.media.thumbnail_path ?? "",
      width: current.media.width ?? undefined,
      height: current.media.height ?? undefined,
      views: 0,
      inserted_at: "",
      is_favorite: false,
    } satisfies MediaPreview,
    effective_caption: current.effective_caption,
    has_ops: false,
    face_summary: { face_count: current.face_bbox ? 1 : 0 },
  }] : [], [current, datasetId]);

  if (loading) return <Box minHeight="70vh" display="grid" sx={{ placeItems: "center" }}><CircularProgress /></Box>;

  return (
    <Box sx={{ height: { xs: "auto", md: "calc(100vh - 64px)" }, minHeight: 650, bgcolor: "grey.950", color: "common.white", display: "flex", flexDirection: "column" }}>
      {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}
      {notice && <Alert severity="success" onClose={() => setNotice(null)}>{notice}</Alert>}
      <Box sx={{ flex: 1, minHeight: 0, display: "grid", gridTemplateColumns: { xs: "1fr", md: "minmax(0, 1fr) 370px" } }}>
        <Box ref={imagePanelRef} sx={{ minHeight: { xs: 420, md: 0 }, display: "grid", placeItems: "center", overflow: "hidden", position: "relative" }}>
          {!current ? (
            <Stack spacing={2} alignItems="center">
              <Typography variant="h5">No items match this filter.</Typography>
              <Button component={RouterLink} to={`/dataset/${datasetId}`} variant="outlined">Back to dataset</Button>
            </Stack>
          ) : imageSize.width > 0 && (
            <Box sx={{ width: imageSize.width, height: imageSize.height, position: "relative" }}>
              <Box component="img" src={`${API}${current.media.original_url}`} alt={current.media.filename} sx={{ width: "100%", height: "100%", objectFit: "contain", display: "block" }} />
              {current.face_bbox && current.media.width && current.media.height && (
                <Box sx={{ position: "absolute", pointerEvents: "none", border: "2px solid", borderColor: "warning.main", boxShadow: "0 0 0 1px rgba(0,0,0,.7)", left: `${(current.face_bbox[0] / current.media.width) * 100}%`, top: `${(current.face_bbox[1] / current.media.height) * 100}%`, width: `${(current.face_bbox[2] / current.media.width) * 100}%`, height: `${(current.face_bbox[3] / current.media.height) * 100}%` }} />
              )}
              <Chip label={`#${current.item.position + 1}`} size="small" sx={{ position: "absolute", top: 12, left: 12, bgcolor: "rgba(0,0,0,.65)", color: "common.white" }} />
            </Box>
          )}
        </Box>

        <Paper square elevation={8} sx={{ bgcolor: "background.paper", color: "text.primary", p: 2.5, overflowY: "auto" }}>
          <Stack spacing={2.5}>
            <Stack direction="row" spacing={1} alignItems="center">
              <Button component={RouterLink} to={`/dataset/${datasetId}`} size="small">← Dataset</Button>
              <FormControl size="small" sx={{ minWidth: 125, ml: "auto" }}>
                <InputLabel>Filter</InputLabel>
                <Select label="Filter" value={filter} onChange={(event) => setSearchParams(event.target.value === "all" ? {} : { filter: event.target.value })}>
                  <MenuItem value="all">All</MenuItem>
                  <MenuItem value="findings">Findings</MenuItem>
                  <MenuItem value="excluded">Excluded</MenuItem>
                </Select>
              </FormControl>
            </Stack>
            <Box>
              <Stack direction="row" justifyContent="space-between"><Typography fontWeight={700}>{reviewedCount} / {totalCount} reviewed</Typography><Typography variant="caption">Queue {entries.length ? currentIndex + 1 : 0} / {entries.length}{nextCursor ? "+" : ""}</Typography></Stack>
              <LinearProgress variant="determinate" value={totalCount ? reviewedCount / totalCount * 100 : 0} sx={{ mt: 1 }} />
            </Box>
            {current && <>
              <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                <Chip color={current.item.excluded ? "error" : current.item.reviewed_at ? "success" : "default"} label={current.item.excluded ? "Excluded" : current.item.reviewed_at ? "Reviewed" : "Unreviewed"} />
                <Chip label={current.metrics.framing.replace("_", " ")} color="primary" variant="outlined" />
                <Chip label={`Weight ${current.item.weight}`} variant="outlined" />
              </Stack>
              <Box sx={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 1.5 }}>
                {[
                  ["Sharpness", displayNumber(current.metrics.sharpness, 1)],
                  ["Frontality", displayNumber(current.metrics.frontality)],
                  ["Face ratio", displayNumber(current.metrics.face_ratio, 3)],
                  ["Identity distance", displayNumber(current.metrics.identity_distance, 3)],
                  ["Brightness", displayNumber(current.metrics.brightness, 1)],
                ].map(([label, value]) => <Box key={label}><Typography variant="caption" color="text.secondary">{label}</Typography><Typography fontWeight={650}>{value}</Typography></Box>)}
              </Box>
              <Box>
                <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}><Typography fontWeight={700}>Caption</Typography><Chip size="small" label={current.caption_source} /></Stack>
                <TextField inputRef={captionRef} value={caption} onChange={(event) => setCaption(event.target.value)} onBlur={() => void saveCaption()} multiline minRows={4} fullWidth placeholder="Caption body" />
                {current.findings.length > 0 && <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap mt={1}>{current.findings.map((finding, index) => <Chip key={`${finding.code}-${index}`} size="small" label={finding.code} title={finding.message} color={finding.severity === "error" ? "error" : finding.severity === "warn" ? "warning" : "info"} variant="outlined" />)}</Stack>}
                {current.effective_caption && <Typography variant="caption" color="text.secondary" display="block" mt={1}>Effective: {current.effective_caption}</Typography>}
              </Box>
              <Stack direction="row" spacing={1}>
                {[0.5, 1, 1.5].map((weight, index) => <Button key={weight} variant={current.item.weight === weight ? "contained" : "outlined"} onClick={() => void setWeight(weight)}>{index + 1} · {weight}</Button>)}
              </Stack>
            </>}
          </Stack>
        </Paper>
      </Box>

      <Box sx={{ px: 2, py: 1.25, bgcolor: "rgba(15,15,18,.98)", borderTop: 1, borderColor: "grey.800", overflowX: "auto" }}>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ minWidth: "max-content" }}>
          <Button variant="contained" color="success" disabled={!current || busy} onClick={() => void keep()}>K Keep</Button>
          <Button variant="contained" color="error" disabled={!current || busy} onClick={() => void exclude()}>X Exclude</Button>
          <Button disabled={!current?.face_crop_suggestion || busy} onClick={() => setCropOpen(true)}>C Crop</Button>
          <Button disabled={!current} onClick={() => captionRef.current?.focus()}>E Caption</Button>
          <Button ref={repairButtonRef} disabled={!current || !config.REPAIRS_ENABLED || busy} onClick={(event) => setRepairAnchor(event.currentTarget)}>R Repair</Button>
          <Button disabled={currentIndex === 0} onClick={() => move(-1)}>←</Button>
          <Button disabled={!current || (currentIndex === entries.length - 1 && !nextCursor)} onClick={() => move(1)}>→</Button>
          <Button disabled={!history.length || busy} onClick={() => void undo()}>U Undo</Button>
          <Button onClick={() => setHelpOpen(true)}>?</Button>
          {loadingMore && <CircularProgress size={20} />}
        </Stack>
      </Box>

      <Menu anchorEl={repairAnchor} open={Boolean(repairAnchor)} onClose={() => setRepairAnchor(null)}>
        <MenuItem onClick={() => void beginRepair("omoide-remove-text-v1")}>Remove overlays</MenuItem>
        <MenuItem onClick={() => void beginRepair("omoide-upscale-v1")}>Upscale</MenuItem>
        {personId != null && <MenuItem onClick={() => void beginRepair("omoide-remove-people-v1")}>Remove other people</MenuItem>}
      </Menu>
      <BatchCropDialog open={cropOpen} datasetId={datasetId} personId={personId} items={cropItem} itemIds={current ? [current.item.id] : []} acceptOnEnter onClose={() => setCropOpen(false)} onApplied={() => setNotice("Crop suggestion applied")} />
      <Dialog open={helpOpen} onClose={() => setHelpOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>Keyboard triage</DialogTitle>
        <DialogContent><Stack spacing={1}>{HOTKEYS.map(([key, label]) => <Stack key={key} direction="row" spacing={2}><Chip label={key} size="small" sx={{ minWidth: 82 }} /><Typography>{label}</Typography></Stack>)}</Stack><Typography variant="body2" color="text.secondary" mt={2}>Hotkeys pause while you type. Escape leaves the caption field.</Typography></DialogContent>
        <DialogActions><Button onClick={() => setHelpOpen(false)}>Close</Button></DialogActions>
      </Dialog>
    </Box>
  );
}
