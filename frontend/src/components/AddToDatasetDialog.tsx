import { useUndo } from "../context/UndoContext";
import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  List,
  ListItemButton,
  ListItemText,
  Typography,
} from "@mui/material";
import type { TrainingDataset } from "../types";
import { addDatasetItems, getDatasets, removeDatasetItems } from "../services/datasets";
import NewDatasetDialog from "./NewDatasetDialog";

interface Props {
  open: boolean;
  mediaIds: number[];
  onClose: () => void;
  onAdded?: (dataset: TrainingDataset, count: number) => void;
}

export default function AddToDatasetDialog({ open, mediaIds, onClose, onAdded }: Props) {
  const { push, refreshVisible } = useUndo();
  const [error, setError] = useState<string | null>(null);
  const [datasets, setDatasets] = useState<TrainingDataset[]>([]);
  const [newOpen, setNewOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) {
      setError(null);
      void getDatasets().then(setDatasets).catch((reason) => setError(reason instanceof Error ? reason.message : "Failed to load datasets"));
    }
  }, [open]);

  const add = async (dataset: TrainingDataset) => {
    setBusy(true);
    setError(null);
    try {
      const result = await addDatasetItems(dataset.id, mediaIds);
      const addedIds = [...result.added_ids];
      if (addedIds.length) push({
        label: `Added ${addedIds.length} item(s) to ${dataset.name}`,
        undo: async () => {
          const inverse = await removeDatasetItems(dataset.id, addedIds);
          await refreshVisible();
          if (inverse.skipped_ids.length) throw new Error(`${inverse.skipped_ids.length} item(s) could not be removed`);
        },
      });
      onAdded?.(dataset, result.added_ids.length);
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to add to dataset");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Dialog open={open} onClose={busy ? undefined : onClose} fullWidth maxWidth="xs">
        <DialogTitle>Add to dataset</DialogTitle>
        <DialogContent dividers>
          {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
          {datasets.length === 0 ? (
            <Typography color="text.secondary">No datasets yet.</Typography>
          ) : (
            <List disablePadding>
              {datasets.map((dataset) => (
                <ListItemButton key={dataset.id} disabled={busy} onClick={() => void add(dataset)}>
                  <ListItemText primary={dataset.name} secondary={`${dataset.item_count} items · ${dataset.trigger_word}`} />
                </ListItemButton>
              ))}
            </List>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={() => setNewOpen(true)} disabled={busy}>New dataset…</Button>
        </DialogActions>
      </Dialog>
      <NewDatasetDialog
        open={newOpen}
        onClose={() => setNewOpen(false)}
        onCreated={(dataset) => {
          setNewOpen(false);
          setDatasets((current) => [dataset, ...current]);
          void add(dataset);
        }}
      />
    </>
  );
}
