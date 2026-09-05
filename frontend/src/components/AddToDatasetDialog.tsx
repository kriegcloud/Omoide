import { useEffect, useState } from "react";
import {
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
import { addDatasetItems, getDatasets } from "../services/datasets";
import NewDatasetDialog from "./NewDatasetDialog";

interface Props {
  open: boolean;
  mediaIds: number[];
  onClose: () => void;
  onAdded?: (dataset: TrainingDataset, count: number) => void;
}

export default function AddToDatasetDialog({ open, mediaIds, onClose, onAdded }: Props) {
  const [datasets, setDatasets] = useState<TrainingDataset[]>([]);
  const [newOpen, setNewOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) void getDatasets().then(setDatasets);
  }, [open]);

  const add = async (dataset: TrainingDataset) => {
    setBusy(true);
    try {
      const result = await addDatasetItems(dataset.id, mediaIds);
      onAdded?.(dataset, result.added_ids.length);
      onClose();
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Dialog open={open} onClose={busy ? undefined : onClose} fullWidth maxWidth="xs">
        <DialogTitle>Add to dataset</DialogTitle>
        <DialogContent dividers>
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
          <Button onClick={onClose}>Cancel</Button>
          <Button onClick={() => setNewOpen(true)}>New dataset…</Button>
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
