import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Container,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import SaveIcon from "@mui/icons-material/Save";
import FileDownloadIcon from "@mui/icons-material/FileDownload";
import { useParams } from "react-router-dom";
import ImageEditorDialog from "../components/ImageEditorDialog";
import MarqueeSelectionBox from "../components/MarqueeSelectionBox";
import MediaCard from "../components/MediaCard";
import { useSelection } from "../context/SelectionContext";
import { useMarqueeSelection } from "../hooks/useMarqueeSelection";
import {
  createDatasetExport,
  datasetManifestUrl,
  getDataset,
  getDatasetExports,
  getDatasetItems,
  removeDatasetItems,
  updateDataset,
  updateDatasetItem,
} from "../services/datasets";
import type { DatasetExport, DatasetExportLayout, DatasetItem, Media, TrainingDataset } from "../types";
import type { FilerobotDesignState } from "../utils/editorOps";

export default function DatasetDetailPage() {
  const { id } = useParams();
  const datasetId = Number(id);
  const [dataset, setDataset] = useState<TrainingDataset | null>(null);
  const [items, setItems] = useState<DatasetItem[]>([]);
  const [exports, setExports] = useState<DatasetExport[]>([]);
  const [tab, setTab] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [captionItem, setCaptionItem] = useState<DatasetItem | null>(null);
  const [caption, setCaption] = useState("");
  const [cropItem, setCropItem] = useState<DatasetItem | null>(null);
  const [exportLayout, setExportLayout] = useState<DatasetExportLayout>("ai_toolkit");
  const gridRef = useRef<HTMLDivElement>(null);
  const selection = useSelection();
  const { marqueeRect, onItemClick } = useMarqueeSelection<number>({
    containerRef: gridRef,
    itemSelector: "[data-media-card]",
    getId: (element) => Number(element.dataset.selectableId),
    enabled: selection.isSelecting,
    selectedIds: selection.selectedIds,
    onSelectionChange: selection.setSelected,
  });

  const load = useCallback(async () => {
    try {
      const [nextDataset, page, history] = await Promise.all([
        getDataset(datasetId),
        getDatasetItems(datasetId),
        getDatasetExports(datasetId),
      ]);
      setDataset(nextDataset);
      setItems(page.items);
      setExports(history);
      setExportLayout(nextDataset.export_layout);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to load dataset");
    } finally {
      setLoading(false);
    }
  }, [datasetId]);

  useEffect(() => { void load(); return () => selection.clear(); }, [load]);
  const hasRunning = exports.some((entry) => entry.status === "pending" || entry.status === "running");
  useEffect(() => {
    if (!hasRunning) return;
    const timer = window.setInterval(() => void getDatasetExports(datasetId).then(setExports), 3000);
    return () => window.clearInterval(timer);
  }, [datasetId, hasRunning]);

  const patchItem = async (item: DatasetItem, input: Parameters<typeof updateDatasetItem>[2]) => {
    const updated = await updateDatasetItem(datasetId, item.id, input);
    setItems((current) => current.map((entry) => entry.id === updated.id ? updated : entry));
  };
  const selectedItems = useMemo(() => items.filter((item) => selection.selectedIds.has(item.media_id)), [items, selection.selectedIds]);
  const bulkExcluded = async (excluded: boolean) => {
    await Promise.all(selectedItems.map((item) => updateDatasetItem(datasetId, item.id, { excluded })));
    setItems((current) => current.map((item) => selection.selectedIds.has(item.media_id) ? { ...item, excluded } : item));
    selection.clear();
  };
  const remove = async (targets: DatasetItem[]) => {
    await removeDatasetItems(datasetId, targets.map((item) => item.media_id));
    const ids = new Set(targets.map((item) => item.id));
    setItems((current) => current.filter((item) => !ids.has(item.id)));
    selection.clear();
  };

  if (loading || !dataset) return <Box minHeight="60vh" display="grid" sx={{ placeItems: "center" }}><CircularProgress /></Box>;

  return (
    <Container maxWidth="xl" sx={{ py: 4, pb: 12 }}>
      <Paper elevation={0} sx={{ p: { xs: 2, md: 3 }, mb: 3, border: 1, borderColor: "divider", borderRadius: 3 }}>
        <Stack direction={{ xs: "column", md: "row" }} spacing={2} alignItems={{ md: "flex-start" }}>
          <Box flex={1}>
            <TextField label="Dataset name" value={dataset.name} onChange={(event) => setDataset({ ...dataset, name: event.target.value })} variant="standard" fullWidth inputProps={{ style: { fontSize: 28, fontWeight: 700 } }} />
            <Stack direction={{ xs: "column", sm: "row" }} spacing={2} mt={2}>
              <TextField label="Trigger word" value={dataset.trigger_word} onChange={(event) => setDataset({ ...dataset, trigger_word: event.target.value })} fullWidth />
              <TextField label="Class" value={dataset.class_token} onChange={(event) => setDataset({ ...dataset, class_token: event.target.value })} fullWidth />
              <FormControl fullWidth><InputLabel>Caption source</InputLabel><Select label="Caption source" value={dataset.caption_source} onChange={(event) => setDataset({ ...dataset, caption_source: event.target.value as TrainingDataset["caption_source"] })}><MenuItem value="annotation">Annotation</MenuItem><MenuItem value="template">Template only</MenuItem><MenuItem value="none">None</MenuItem></Select></FormControl>
            </Stack>
            <TextField sx={{ mt: 2 }} label="Caption template" value={dataset.caption_template} onChange={(event) => setDataset({ ...dataset, caption_template: event.target.value })} fullWidth disabled={dataset.caption_source === "none"} />
            <Stack direction={{ xs: "column", sm: "row" }} spacing={2} mt={2}>
              <TextField type="number" label="Target resolution" value={dataset.target_resolution} onChange={(event) => setDataset({ ...dataset, target_resolution: Number(event.target.value) })} />
              <TextField label="Buckets" value={dataset.buckets.join(", ")} onChange={(event) => setDataset({ ...dataset, buckets: event.target.value.split(",").map(Number).filter(Boolean) })} helperText="Comma-separated long sides" />
              <TextField type="number" label="Repeats" value={dataset.repeats} onChange={(event) => setDataset({ ...dataset, repeats: Number(event.target.value) })} />
              <FormControl sx={{ minWidth: 160 }}><InputLabel>Default layout</InputLabel><Select label="Default layout" value={dataset.export_layout} onChange={(event) => setDataset({ ...dataset, export_layout: event.target.value as DatasetExportLayout })}><MenuItem value="ai_toolkit">ai-toolkit</MenuItem><MenuItem value="kohya">Kohya</MenuItem><MenuItem value="onetrainer">OneTrainer</MenuItem></Select></FormControl>
            </Stack>
          </Box>
          <Button startIcon={<SaveIcon />} variant="contained" disabled={saving} onClick={async () => { setSaving(true); try { setDataset(await updateDataset(datasetId, dataset)); } catch (reason) { setError(reason instanceof Error ? reason.message : "Failed to save dataset"); } finally { setSaving(false); } }}>Save</Button>
        </Stack>
      </Paper>
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
      <Tabs value={tab} onChange={(_, value) => setTab(value)} sx={{ mb: 2 }}><Tab label={`Items (${items.length})`} /><Tab label={`Exports (${exports.length})`} /></Tabs>

      {tab === 0 && (
        <>
          <Stack direction="row" spacing={1} alignItems="center" mb={2} flexWrap="wrap" useFlexGap>
            <Button variant="outlined" onClick={selection.toggleSelecting}>{selection.isSelecting ? "Cancel selection" : "Select items"}</Button>
            {selection.isSelecting && <Typography color="text.secondary">{selection.selectedIds.size} selected</Typography>}
            {selection.isSelecting && <><Button disabled={!selectedItems.length} onClick={() => void bulkExcluded(true)}>Exclude</Button><Button disabled={!selectedItems.length} onClick={() => void bulkExcluded(false)}>Include</Button><Button color="error" disabled={!selectedItems.length} onClick={() => void remove(selectedItems)}>Remove</Button></>}
          </Stack>
          {items.length === 0 ? <Typography color="text.secondary">Add images from any media grid to start curating this dataset.</Typography> : (
            <Box ref={gridRef} sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "repeat(2, 1fr)", sm: "repeat(3, 1fr)", md: "repeat(4, 1fr)", lg: "repeat(5, 1fr)" }, position: "relative" }}>
              {items.map((item) => (
                <MediaCard key={item.id} media={item.media} onSelectionClick={onItemClick} datasetContext={{
                  caption: item.effective_caption, excluded: item.excluded, hasOps: item.has_ops,
                  detScore: item.face_summary.det_score, frontality: item.face_summary.frontality, faceCount: item.face_summary.face_count,
                  onToggleExcluded: () => void patchItem(item, { excluded: !item.excluded }),
                  onEditCaption: () => { setCaptionItem(item); setCaption(item.caption_override ?? item.effective_caption ?? ""); },
                  onEditCrop: () => setCropItem(item), onRemove: () => void remove([item]),
                }} />
              ))}
              <MarqueeSelectionBox container={gridRef.current} rect={marqueeRect} />
            </Box>
          )}
        </>
      )}

      {tab === 1 && (
        <Stack spacing={2}>
          <Stack direction="row" spacing={1} alignItems="center">
            <FormControl size="small" sx={{ minWidth: 160 }}><InputLabel>Layout</InputLabel><Select label="Layout" value={exportLayout} onChange={(event) => setExportLayout(event.target.value as DatasetExportLayout)}><MenuItem value="ai_toolkit">ai-toolkit</MenuItem><MenuItem value="kohya">Kohya</MenuItem><MenuItem value="onetrainer">OneTrainer</MenuItem></Select></FormControl>
            <Button variant="contained" startIcon={<FileDownloadIcon />} onClick={async () => {
              try {
                const created = await createDatasetExport(datasetId, exportLayout);
                setExports((current) => [created, ...current]);
              } catch (reason) {
                setError(reason instanceof Error ? reason.message : "Failed to start export");
              }
            }}>Export now</Button>
          </Stack>
          {exports.length === 0 ? <Typography color="text.secondary">No exports yet.</Typography> : exports.map((entry) => (
            <Paper key={entry.id} elevation={0} sx={{ p: 2, border: 1, borderColor: "divider", borderRadius: 3 }}>
              <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" gap={1}>
                <Box><Stack direction="row" spacing={1} alignItems="center"><Typography fontWeight={700}>{entry.layout}</Typography><Chip size="small" label={entry.status} color={entry.status === "completed" ? "success" : entry.status === "failed" ? "error" : "default"} /></Stack><Typography variant="body2" color="text.secondary">{entry.item_count} items · {entry.host_output_dir || entry.output_dir || "Preparing output…"}</Typography>{entry.error && <Typography variant="body2" color="error">{entry.error}</Typography>}{entry.launch_command && <Typography component="code" variant="caption" display="block" mt={1}>{entry.launch_command}</Typography>}</Box>
                {entry.manifest && <Button component="a" href={datasetManifestUrl(entry.id)} target="_blank" rel="noreferrer">Manifest</Button>}
              </Stack>
            </Paper>
          ))}
        </Stack>
      )}

      <Dialog open={Boolean(captionItem)} onClose={() => setCaptionItem(null)} fullWidth maxWidth="sm"><DialogTitle>Edit caption</DialogTitle><DialogContent><TextField autoFocus multiline minRows={3} fullWidth value={caption} onChange={(event) => setCaption(event.target.value)} sx={{ mt: 1 }} /></DialogContent><DialogActions><Button onClick={() => setCaptionItem(null)}>Cancel</Button><Button variant="contained" onClick={async () => { if (captionItem) await patchItem(captionItem, { caption_override: caption.trim() || null }); setCaptionItem(null); }}>Save caption</Button></DialogActions></Dialog>
      {cropItem && <ImageEditorDialog
        open media={{ ...cropItem.media, tags: [], faces: [], extracted_scenes: false } as Media}
        mode="virtual" loadableDesignState={(cropItem.edit_design_state as FilerobotDesignState | null) ?? null}
        onClose={() => setCropItem(null)}
        onOpsReady={(ops, designState) => { void patchItem(cropItem, { edit_ops: ops, edit_design_state: designState }); setCropItem(null); }}
      />}
    </Container>
  );
}
