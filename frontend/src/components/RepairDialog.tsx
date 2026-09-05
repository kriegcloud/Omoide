import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Switch,
  TextField,
} from "@mui/material";
import type { RepairProfile } from "../types";
import { listBackgroundPrompts, startBulkRepair, startRepair } from "../services/repairs";

interface Props {
  open: boolean;
  mediaIds: number[];
  personId?: number;
  initialProfile?: RepairProfile;
  onClose: () => void;
  onStarted?: (count: number) => void;
}

export default function RepairDialog({
  open,
  mediaIds,
  personId,
  initialProfile = "omoide-remove-text-v1",
  onClose,
  onStarted,
}: Props) {
  const [profile, setProfile] = useState<RepairProfile>(initialProfile);
  const [prompts, setPrompts] = useState<string[]>([]);
  const [prompt, setPrompt] = useState("");
  const [seed, setSeed] = useState("");
  const [randomizePrompts, setRandomizePrompts] = useState(false);
  const [promptsError, setPromptsError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadPrompts = useCallback(async () => {
    setPromptsError(null);
    try {
      const loaded = await listBackgroundPrompts();
      setPrompts(loaded);
      setPrompt((current) => current || loaded[0] || "");
    } catch (reason) {
      setPromptsError(reason instanceof Error ? reason.message : "Failed to load prompt presets");
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    setProfile(initialProfile);
    setError(null);
    setRandomizePrompts(false);
  }, [initialProfile, open]);

  useEffect(() => {
    if (open && profile === "omoide-background-swap-v1" && prompts.length === 0) {
      void loadPrompts();
    }
  }, [loadPrompts, open, profile, prompts.length]);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const parsedSeed = seed === "" ? undefined : Number(seed);
      if (parsedSeed !== undefined && (!Number.isSafeInteger(parsedSeed) || parsedSeed < 0)) {
        setError("Seed must be a non-negative whole number");
        return;
      }
      const options = {
        personId,
        ...(profile === "omoide-background-swap-v1" && !randomizePrompts
          ? { prompt: prompt.trim(), seed: parsedSeed }
          : {}),
        ...(profile === "omoide-background-swap-v1" && randomizePrompts
          ? { randomizePrompts: true }
          : {}),
      };
      const jobs = mediaIds.length === 1
        ? [await startRepair(mediaIds[0], profile, options)]
        : await startBulkRepair(mediaIds, profile, options);
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
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <Stack spacing={2} sx={{ mt: 0.5 }}>
          <FormControl fullWidth>
            <InputLabel>Repair</InputLabel>
            <Select label="Repair" value={profile} onChange={(event) => setProfile(event.target.value as RepairProfile)}>
              <MenuItem value="omoide-remove-text-v1">Remove overlays</MenuItem>
              <MenuItem value="omoide-upscale-v1">Upscale</MenuItem>
              {personId != null && <MenuItem value="omoide-remove-people-v1">Remove other people</MenuItem>}
              {personId != null && <MenuItem value="omoide-background-swap-v1">Swap background…</MenuItem>}
            </Select>
          </FormControl>
          {profile === "omoide-background-swap-v1" && (
            <>
              {mediaIds.length > 1 && (
                <FormControlLabel
                  control={<Switch checked={randomizePrompts} onChange={(event) => setRandomizePrompts(event.target.checked)} />}
                  label="Randomise prompts across selection"
                />
              )}
              {!randomizePrompts && (
                <>
                  {promptsError && (
                    <Alert severity="warning" action={<Button color="inherit" size="small" onClick={() => void loadPrompts()}>Retry</Button>}>
                      {promptsError}
                    </Alert>
                  )}
                  {prompts.length > 0 && (
                    <FormControl fullWidth>
                      <InputLabel>Prompt preset</InputLabel>
                      <Select
                        label="Prompt preset"
                        value={prompts.includes(prompt) ? prompt : ""}
                        onChange={(event) => setPrompt(event.target.value)}
                      >
                        <MenuItem value=""><em>Custom prompt</em></MenuItem>
                        {prompts.map((preset) => <MenuItem key={preset} value={preset} sx={{ whiteSpace: "normal" }}>{preset}</MenuItem>)}
                      </Select>
                    </FormControl>
                  )}
                  <TextField
                    label="Background prompt"
                    value={prompt}
                    onChange={(event) => setPrompt(event.target.value)}
                    required
                    multiline
                    minRows={3}
                    helperText={`${prompt.length}/2000 characters`}
                    slotProps={{ htmlInput: { maxLength: 2000 } }}
                  />
                  <TextField
                    label="Seed"
                    value={seed}
                    onChange={(event) => setSeed(event.target.value)}
                    type="number"
                    helperText="Leave blank for a random seed"
                    slotProps={{ htmlInput: { min: 0, step: 1 } }}
                  />
                </>
              )}
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button
          variant="contained"
          onClick={() => void submit()}
          disabled={
            busy
            || mediaIds.length === 0
            || (profile === "omoide-background-swap-v1" && !randomizePrompts && !prompt.trim())
          }
        >
          Start
        </Button>
      </DialogActions>
    </Dialog>
  );
}
