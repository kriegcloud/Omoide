import { useState } from "react";
import { Checkbox, FormControlLabel, Radio, RadioGroup, Typography } from "@mui/material";
import type { VideoConversionMode } from "../types";
import ConfirmDialog from "./ConfirmDialog";

interface ActionDialogsProps {
  dialogType: "convert" | "deleteRecord" | "deleteFile" | null;
  loading?: boolean;
  onClose: () => void;
  onConfirmConvert: (mode: Exclude<VideoConversionMode, "off">) => void;
  onConfirmDeleteRecord: () => void;
  onConfirmDeleteFile: () => void;
}

export function ActionDialogs({ dialogType, loading, onClose, onConfirmConvert, onConfirmDeleteRecord, onConfirmDeleteFile }: ActionDialogsProps) {
  const [mode, setMode] = useState<Exclude<VideoConversionMode, "off"> | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const canConvert = mode === "keep_original" || (mode === "replace_original" && acknowledged);
  const actions = {
    convert: { title: "Convert Video Format?", message: "Convert this video to a supported format.", label: "Convert", confirm: () => { if (canConvert && mode) onConfirmConvert(mode); } },
    deleteRecord: { title: "Remove from Library?", message: "The record will be removed from the database. The file on disk is kept and can be re-imported by scanning.", label: "Remove Record", confirm: onConfirmDeleteRecord },
    deleteFile: { title: "Delete File from Disk?", message: "The file will be permanently deleted from disk. This cannot be undone.", label: "Delete File", confirm: onConfirmDeleteFile },
  };
  const action = dialogType ? actions[dialogType] : null;
  return <ConfirmDialog open={action !== null} title={action?.title ?? ""} message={dialogType === "convert" ? <>
      <Typography>Converting creates a playable MP4 and may take significant CPU or GPU time.</Typography>
      <RadioGroup aria-label="Conversion mode" value={mode ?? ""} onChange={e => {setMode(e.target.value as Exclude<VideoConversionMode, "off">); setAcknowledged(false);}}>
        <FormControlLabel value="keep_original" control={<Radio />} label="Keep the original and store a playable copy (uses extra disk space)" />
        <FormControlLabel value="replace_original" control={<Radio />} label="Replace the original after successful conversion" />
      </RadioGroup>
      {mode === "replace_original" && <FormControlLabel control={<Checkbox checked={acknowledged} onChange={e => setAcknowledged(e.target.checked)} />} label="I understand the original file will be permanently deleted." />}
    </> : action?.message ?? ""}
    confirmLabel={action?.label} confirmColor={dialogType === "convert" ? "primary" : "error"}
    loading={loading} confirmDisabled={dialogType === "convert" && !canConvert} onClose={onClose} onConfirm={() => action?.confirm()} />;
}
