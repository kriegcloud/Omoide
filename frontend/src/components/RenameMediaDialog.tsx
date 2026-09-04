import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
} from "@mui/material";

interface RenameMediaDialogProps {
  open: boolean;
  filename: string;
  loading?: boolean;
  error?: string | null;
  onClose: () => void;
  onConfirm: (filename: string) => void;
}

export default function RenameMediaDialog({
  open,
  filename,
  loading = false,
  error,
  onClose,
  onConfirm,
}: RenameMediaDialogProps) {
  const [value, setValue] = useState(filename);

  useEffect(() => {
    if (open) setValue(filename);
  }, [open, filename]);

  const submit = () => {
    const next = value.trim();
    if (next) onConfirm(next);
  };

  return (
    <Dialog open={open} onClose={loading ? undefined : onClose} fullWidth maxWidth="xs">
      <DialogTitle>Rename media</DialogTitle>
      <DialogContent>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <TextField
          autoFocus
          fullWidth
          label="Filename"
          value={value}
          disabled={loading}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") submit();
          }}
          helperText="Leave the extension off to keep the current one."
          sx={{ mt: 1 }}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={loading}>Cancel</Button>
        <Button variant="contained" onClick={submit} disabled={loading || !value.trim()}>
          {loading ? "Renaming…" : "Rename"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
