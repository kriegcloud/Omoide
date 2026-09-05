import { useState } from "react";
import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, FormControl, InputLabel, MenuItem, Select } from "@mui/material";
import type { RepairProfile } from "../types";
import { startBulkRepair, startRepair } from "../services/repairs";

interface Props {
  open: boolean;
  mediaIds: number[];
  personId?: number;
  onClose: () => void;
  onStarted?: (count: number) => void;
}

export default function RepairDialog({ open, mediaIds, personId, onClose, onStarted }: Props) {
  const [profile, setProfile] = useState<RepairProfile>("omoide-remove-text-v1");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const jobs = mediaIds.length === 1
        ? [await startRepair(mediaIds[0], profile, personId)]
        : await startBulkRepair(mediaIds, profile, personId);
      onStarted?.(jobs.length);
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to start repair");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} fullWidth maxWidth="xs">
      <DialogTitle>Repair images</DialogTitle>
      <DialogContent>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <FormControl fullWidth sx={{ mt: 1 }}>
          <InputLabel>Repair</InputLabel>
          <Select label="Repair" value={profile} onChange={(event) => setProfile(event.target.value as RepairProfile)}>
            <MenuItem value="omoide-remove-text-v1">Remove overlays</MenuItem>
            <MenuItem value="omoide-upscale-v1">Upscale</MenuItem>
            {personId != null && <MenuItem value="omoide-remove-people-v1">Remove other people</MenuItem>}
          </Select>
        </FormControl>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="contained" onClick={() => void submit()} disabled={busy || mediaIds.length === 0}>Start</Button>
      </DialogActions>
    </Dialog>
  );
}
