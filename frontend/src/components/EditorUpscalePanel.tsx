import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, Radio, RadioGroup, Stack } from "@mui/material";
import { useNavigate } from "react-router-dom";
import type { ImageRepairJob, Media } from "../types";
import { getMedia } from "../services/media";
import { getRepair, startRepair } from "../services/repairs";
import { useVisiblePolling } from "../hooks/useVisiblePolling";
import { useDialogHotkeyScope } from "../hotkeys/useHotkey";
import UpscalerPicker, { defaultUpscaleSelection } from "./UpscalerPicker";
import BeforeAfterCompare from "./BeforeAfterCompare";

interface Props {
  open: boolean;
  media: Media;
  hasChanges: boolean;
  initialProvider: string | null;
  onProviderChange: (provider: string) => void;
  saveCopy: () => Promise<Media | null>;
  onClose: () => void;
}
const terminal = new Set(["succeeded", "failed", "cancelled", "interrupted"]);

export default function EditorUpscalePanel({ open, media, hasChanges, initialProvider, onProviderChange, saveCopy, onClose }: Props) {
  const navigate = useNavigate();
  const generation = useRef(0);
  const submitting = useRef(false);
  const scope = useDialogHotkeyScope(open);
  const [selection, setSelection] = useState(() => defaultUpscaleSelection(initialProvider || undefined));
  const [valid, setValid] = useState(false);
  const [sourceChoice, setSourceChoice] = useState("copy");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<ImageRepairJob | null>(null);
  const [source, setSource] = useState(media);
  const [comparison, setComparison] = useState<{ before: Media; after: Media } | null>(null);
  const comparisonScope = useDialogHotkeyScope(Boolean(comparison));
  useEffect(() => {
    generation.current += 1;
    submitting.current = false;
    setJob(null); setComparison(null); setSource(media); setBusy(false); setError(null);
    setSelection(defaultUpscaleSelection(initialProvider || undefined));
    return () => { generation.current += 1; };
    // A new media id starts a fresh session, even if this dialog stays mounted.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [media.id]);
  // Consent belongs to one run; reopening never preserves a previous approval.
  useEffect(() => {
    if (open) { setSelection(defaultUpscaleSelection(initialProvider || undefined)); setError(null); setSourceChoice("copy"); }
  }, [open, initialProvider]);
  const poll = useCallback(async (isCurrent: () => boolean) => {
    if (!job || terminal.has(job.status)) return;
    const currentGeneration = generation.current;
    try {
      const updated = await getRepair(job.id);
      if (isCurrent() && currentGeneration === generation.current) { setJob(updated); setError(null); }
    } catch (reason) {
      if (isCurrent() && currentGeneration === generation.current) setError(reason instanceof Error ? reason.message : "Cannot refresh upscale progress");
    }
  }, [job]);
  useVisiblePolling(poll, 3000, Boolean(job && !terminal.has(job.status)));
  const start = async () => {
    if (submitting.current || !valid) return;
    submitting.current = true;
    const currentGeneration = generation.current;
    const isCurrent = () => currentGeneration === generation.current;
    const runSelection = selection;
    setSelection(current => ({ ...current, hostedConsent: false }));
    setBusy(true); setError(null);
    try {
      const input = hasChanges && sourceChoice === "copy" ? await saveCopy() : media;
      if (!isCurrent()) return;
      if (!input) throw new Error("The edited copy could not be saved. Close this panel to review the editor error, then try again.");
      setSource(input);
      onClose();
      const queued = await startRepair(input.id, "omoide-upscale-v1", runSelection);
      if (!isCurrent()) return;
      setJob(queued);
    } catch (reason) { if (isCurrent()) setError(reason instanceof Error ? reason.message : "Cannot start upscale"); }
    finally { if (isCurrent()) { submitting.current = false; setBusy(false); } }
  };
  const compare = async () => {
    if (!job?.result_media_id) return;
    const currentGeneration = generation.current;
    try {
      const detail = await getMedia(String(job.result_media_id));
      if (currentGeneration === generation.current) setComparison({ before: source, after: detail.media });
    } catch (reason) { if (currentGeneration === generation.current) setError(reason instanceof Error ? reason.message : "Cannot open upscale result"); }
  };
  return <>
    {job && <Alert severity={job.status === "failed" ? "error" : job.status === "succeeded" ? "success" : "info"}
      action={<Stack direction="row" spacing={1}>
        {job.status === "succeeded" && job.result_media_id && <Button onClick={() => void compare()}>Compare result</Button>}
        <Button onClick={() => navigate("/repairs")}>Repairs</Button>
      </Stack>}>
      Upscale {job.status}{job.error_message ? `: ${job.error_message}` : ""}. You can continue editing while it runs.
    </Alert>}
    {!open && error && <Alert severity="error">{error}</Alert>}
    <Dialog ref={scope} open={open} onClose={busy ? undefined : onClose} fullWidth maxWidth="sm">
      <DialogTitle>Upscale image</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2}>
          {error && <Alert severity="error">{error}</Alert>}
          {hasChanges && <Alert severity="info">
            You have unsaved edits. Choose the image to upscale.
            <RadioGroup value={sourceChoice} onChange={event => setSourceChoice(event.target.value)}>
              <FormControlLabel disabled={busy} value="copy" control={<Radio />} label="Save copy then upscale the copy" />
              <FormControlLabel disabled={busy} value="original" control={<Radio />} label="Upscale original" />
            </RadioGroup>
          </Alert>}
          <UpscalerPicker value={selection} onChange={next => {
            setSelection(next);
            if (next.provider !== selection.provider) onProviderChange(next.provider);
          }} onValidityChange={setValid} disabled={busy} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="contained" disabled={busy || !valid || Boolean(job && !terminal.has(job.status))} onClick={() => void start()}
          startIcon={busy ? <CircularProgress size={16} /> : undefined}>Start upscale</Button>
      </DialogActions>
    </Dialog>
    <Dialog ref={comparisonScope} open={Boolean(comparison)} onClose={() => setComparison(null)} fullWidth maxWidth="lg">
      <DialogTitle>Before / after upscale</DialogTitle>
      <DialogContent>{comparison && <BeforeAfterCompare before={comparison.before} after={comparison.after} />}</DialogContent>
      <DialogActions>
        <Button onClick={() => setComparison(null)}>Close</Button>
        <Button disabled={!comparison} onClick={() => { if (comparison) navigate(`/medium/${comparison.after.id}`); }}>Open result</Button>
      </DialogActions>
    </Dialog>
  </>;
}
