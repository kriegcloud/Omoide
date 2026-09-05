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
  FormControl,
  FormControlLabel,
  FormLabel,
  InputLabel,
  MenuItem,
  Radio,
  RadioGroup,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import { API } from "../config";
import { batchCropDatasetItems } from "../services/datasets";
import { getFaceCropSuggestions } from "../services/mediaActions";
import type {
  CropAspect,
  CropFraming,
  DatasetBatchCropResult,
  DatasetItem,
  FaceCropSuggestion,
} from "../types";
import { encodeFilePath } from "../urlUtils";

interface BatchCropDialogProps {
  open: boolean;
  datasetId: number;
  personId?: number | null;
  items: DatasetItem[];
  onClose: () => void;
  onApplied: (result: DatasetBatchCropResult) => Promise<void> | void;
}

const FRAMINGS: Array<{ value: CropFraming; label: string }> = [
  { value: "closeup", label: "Close-up" },
  { value: "portrait", label: "Portrait" },
  { value: "half_body", label: "Half body" },
  { value: "full_body", label: "Full body" },
];
const ASPECTS: CropAspect[] = ["free", "1:1", "2:3", "3:4", "4:5", "9:16"];

export default function BatchCropDialog({
  open,
  datasetId,
  personId,
  items,
  onClose,
  onApplied,
}: BatchCropDialogProps) {
  const [framing, setFraming] = useState<CropFraming>("portrait");
  const [aspect, setAspect] = useState<CropAspect>("2:3");
  const [overwrite, setOverwrite] = useState(false);
  const [suggestions, setSuggestions] = useState<Record<number, FaceCropSuggestion | null>>({});
  const [previewing, setPreviewing] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const previewItems = useMemo(() => items.slice(0, 12), [items]);

  useEffect(() => {
    if (!open) return;
    let active = true;
    setPreviewing(true);
    setError(null);
    void Promise.all(
      previewItems.map(async (item) => {
        if (!item.media.width || !item.media.height) return [item.id, null] as const;
        const results = await getFaceCropSuggestions(
          item.media_id,
          framing,
          aspect,
          personId
        );
        const subject = personId == null
          ? results[0]
          : results.find((entry) => entry.person_id === personId);
        return [item.id, subject ?? null] as const;
      })
    )
      .then((entries) => {
        if (active) setSuggestions(Object.fromEntries(entries));
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : "Failed to preview crops");
      })
      .finally(() => {
        if (active) setPreviewing(false);
      });
    return () => { active = false; };
  }, [open, previewItems, framing, aspect, personId]);

  const apply = async () => {
    setApplying(true);
    setError(null);
    try {
      const result = await batchCropDatasetItems(datasetId, {
        framing,
        aspect,
        overwrite_existing_ops: overwrite,
      });
      await onApplied(result);
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to apply batch crops");
    } finally {
      setApplying(false);
    }
  };

  return (
    <Dialog open={open} onClose={applying ? undefined : onClose} fullWidth maxWidth="md">
      <DialogTitle>Batch crop</DialogTitle>
      <DialogContent>
        <Stack spacing={2.5} sx={{ mt: 0.5 }}>
          <FormControl>
            <FormLabel>Framing</FormLabel>
            <RadioGroup row value={framing} onChange={(event) => setFraming(event.target.value as CropFraming)}>
              {FRAMINGS.map((entry) => (
                <FormControlLabel key={entry.value} value={entry.value} control={<Radio />} label={entry.label} />
              ))}
            </RadioGroup>
          </FormControl>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ sm: "center" }}>
            <FormControl size="small" sx={{ minWidth: 160 }}>
              <InputLabel>Aspect</InputLabel>
              <Select label="Aspect" value={aspect} onChange={(event) => setAspect(event.target.value as CropAspect)}>
                {ASPECTS.map((value) => <MenuItem key={value} value={value}>{value === "free" ? "Free" : value}</MenuItem>)}
              </Select>
            </FormControl>
            <FormControlLabel
              control={<Checkbox checked={overwrite} onChange={(event) => setOverwrite(event.target.checked)} />}
              label="Overwrite existing crops"
            />
          </Stack>
          {error && <Alert severity="error">{error}</Alert>}
          <Box>
            <Stack direction="row" spacing={1} alignItems="center" mb={1}>
              <Typography variant="subtitle2">Preview · first {previewItems.length} items</Typography>
              {previewing && <CircularProgress size={16} />}
            </Stack>
            <Box sx={{ display: "grid", gridTemplateColumns: { xs: "repeat(2, 1fr)", sm: "repeat(3, 1fr)", md: "repeat(4, 1fr)" }, gap: 1.5 }}>
              {previewItems.map((item) => {
                const suggestion = suggestions[item.id];
                const width = item.media.width ?? 1;
                const height = item.media.height ?? 1;
                const crop = suggestion?.crop_op;
                return (
                  <Box key={item.id}>
                    <Box sx={{ position: "relative", width: "100%", aspectRatio: `${width} / ${height}`, bgcolor: "action.hover", overflow: "hidden", borderRadius: 1 }}>
                      {item.media.thumbnail_path && (
                        <Box component="img" src={`${API}/thumbnails/${encodeFilePath(item.media.thumbnail_path)}`} alt={item.media.filename} sx={{ width: "100%", height: "100%", display: "block" }} />
                      )}
                      {crop && (
                        <Box sx={{ position: "absolute", pointerEvents: "none", border: "2px solid", borderColor: "primary.light", boxShadow: "0 0 0 9999px rgba(0,0,0,0.28)", left: `${(crop.x / width) * 100}%`, top: `${(crop.y / height) * 100}%`, width: `${(crop.width / width) * 100}%`, height: `${(crop.height / height) * 100}%` }} />
                      )}
                    </Box>
                    <Typography variant="caption" color="text.secondary" noWrap display="block">
                      {suggestion ? `${suggestion.output.width}×${suggestion.output.height}` : "No subject face"}
                    </Typography>
                  </Box>
                );
              })}
            </Box>
          </Box>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={applying}>Cancel</Button>
        <Button variant="contained" onClick={() => void apply()} disabled={applying || items.length === 0 || personId == null}>
          {applying ? "Applying…" : "Apply"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
