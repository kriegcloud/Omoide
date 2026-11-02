import React, { useState } from "react";
import {
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  FormGroup,
  Typography,
} from "@mui/material";
import { runProcessorsForMedia } from "../services/taskActions";

const PROCESSORS = [
  { name: "faces", label: "Face Detection" },
  { name: "embedding_extractor", label: "Image Embeddings" },
  { name: "auto_tagger", label: "Auto Tags" },
  { name: "blur", label: "Blur Score" },
  { name: "exif", label: "EXIF Data" },
];

interface Props {
  open: boolean;
  mediaIds: number[];
  onClose: () => void;
  onStarted: () => void;
}

export const RerunProcessorsDialog: React.FC<Props> = ({
  open,
  mediaIds,
  onClose,
  onStarted,
}) => {
  const [selected, setSelected] = useState<Set<string>>(new Set(["faces"]));

  const toggle = (name: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const handleRun = () => {
    if (selected.size === 0) return;
    const ids = [...mediaIds], names = Array.from(selected);
    onClose();
    void Promise.resolve().then(() => runProcessorsForMedia(ids, names)).then(onStarted).catch(() => {});
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>
        Rerun Processors
        <Typography variant="body2" color="text.secondary" mt={0.5}>
          {mediaIds.length} item{mediaIds.length !== 1 ? "s" : ""} selected
        </Typography>
      </DialogTitle>
      <DialogContent>
        <FormGroup>
          {PROCESSORS.map((p) => (
            <FormControlLabel
              key={p.name}
              control={
                <Checkbox
                  checked={selected.has(p.name)}
                  onChange={() => toggle(p.name)}
                />
              }
              label={p.label}
            />
          ))}
        </FormGroup>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>
          Cancel
        </Button>
        <Button
          onClick={handleRun}
          variant="contained"
          disabled={selected.size === 0}
        >
          Run
        </Button>
      </DialogActions>
    </Dialog>
  );
};
