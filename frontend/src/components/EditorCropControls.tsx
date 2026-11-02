import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Box, Button, FormControlLabel, Stack, Switch, TextField } from "@mui/material";
import type { CropFraming } from "../types";
import { constrainCrop, fitCrop, readCropFields, resolveCropRatio, writeCropFields, type CropRect, type CropSize, type CropStore } from "../utils/editorCrop";

interface Props {
  image: CropSize;
  ratio: string;
  advanced: boolean;
  capture: () => Promise<CropStore | null>;
  update: (fn: (state: CropStore) => Record<string, unknown> | null) => void;
  onOptions: (options: { ratio?: string; advanced?: boolean }) => void;
  onChanged: () => void;
  onFraming: (framing: CropFraming) => void;
}
const landscape = ["16:9", "7:5", "3:2", "4:3", "5:4"];
const portrait = ["9:16", "5:7", "2:3", "3:4", "4:5"];
const standard = new Set(["free", "current", "1:1", "screen", ...landscape, ...portrait, "iso-landscape", "iso-portrait", "letter-landscape", "letter-portrait"]);
const empty = { x: 0, y: 0, width: 1, height: 1 };

export default function EditorCropControls({ image, ratio, advanced, capture, update, onOptions, onChanged, onFraming }: Props) {
  const [rect, setRect] = useState<CropRect>(empty);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ratioText, setRatioText] = useState(ratio);
  const [fields, setFields] = useState<Record<keyof CropRect, string>>({ x: "0", y: "0", width: "1", height: "1" });
  const focused = useRef<string | null>(null);
  const applied = useRef<string | null>(null);
  const screenSize = () => ({ width: window.screen.width || image.width, height: window.screen.height || image.height });
  const apply = useCallback((next: CropRect, nextRatio: string, preserveLive = false) => {
    try {
      const numeric = resolveCropRatio(nextRatio, image, { width: window.screen.width || image.width, height: window.screen.height || image.height });
      update(state => writeCropFields(state, image, preserveLive ? readCropFields(state, image) ?? next : next, numeric));
      setError(null); onChanged();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Cannot change crop"); }
  }, [image, update, onChanged]);
  // Read after drag, undo, zoom and external presets. A null reducer payload
  // observes the live store without inserting history or rerendering Filerobot.
  useEffect(() => {
    let stopped = false, timer: number;
    const read = async () => {
      const live = await capture();
      if (stopped) return;
      const next = live && readCropFields(live, image);
      if (next) {
        setReady(true);
        setRect(old => (Object.keys(next) as (keyof CropRect)[]).every(key => Math.abs(old[key] - next[key]) < .01) ? old : next);
        setFields(old => {
          const copy = { ...old };
          for (const key of Object.keys(next) as (keyof CropRect)[]) if (focused.current !== key) copy[key] = String(Math.round(next[key]));
          return (Object.keys(copy) as (keyof CropRect)[]).every(key => copy[key] === old[key]) ? old : copy;
        });
      }
      timer = window.setTimeout(() => void read(), 150);
    };
    void read();
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [capture, image]);
  useEffect(() => {
    try {
      const numeric = resolveCropRatio(ratio, image, screenSize());
      setRatioText(ratio === "free" ? "" : ratio.includes(":") ? ratio : `${numeric}:1`);
    } catch { setRatioText(ratio); }
    if (!ready || applied.current === ratio) return;
    try {
      const numeric = resolveCropRatio(ratio, image, screenSize());
      // Free preserves the initial rectangle, including restored virtual edits.
      if (ratio !== "free" || applied.current !== null) apply(ratio === "free" ? rect : fitCrop(image, numeric), ratio, ratio === "free");
      applied.current = ratio;
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Invalid crop ratio"); }
  // Geometry is read live; changing it must not reapply a centered preset.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ratio, ready]);
  const choose = (value: string) => {
    if (value.startsWith("framing:")) { onFraming(value.slice(8) as CropFraming); return; }
    if (value !== "custom") onOptions({ ratio: value });
  };
  const commitRatio = () => {
    if (!ratioText.trim() || ratioText.trim() === ratio) return;
    try { resolveCropRatio(ratioText, image, screenSize()); choose(ratioText.trim()); setError(null); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Invalid crop ratio"); }
  };
  const commitField = (key: keyof CropRect) => {
    const value = Number(fields[key]);
    if (!fields[key].trim() || !Number.isFinite(value) || (key === "width" || key === "height" ? value < 1 : value < 0)) { setError("Size must be positive and position must be zero or greater."); return; }
    const next = { ...rect, [key]: value };
    try {
      const numeric = resolveCropRatio(ratio, image, screenSize());
      if (numeric && key === "width") next.height = value / numeric;
      if (numeric && key === "height") next.width = value * numeric;
      // Fit both dimensions together to retain the selected ratio at boundaries.
      const factor = Math.min(1, image.width / next.width, image.height / next.height);
      next.width *= factor; next.height *= factor;
      apply(constrainCrop(next, image), ratio);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Invalid geometry"); }
  };
  return <Box sx={{ px: 2, py: 1, borderBottom: 1, borderColor: "divider", bgcolor: "background.paper", maxHeight: "35vh", overflowY: "auto" }}>
    <Stack direction="row" useFlexGap flexWrap="wrap" spacing={2} alignItems="center">
      <TextField select size="small" label="Aspect ratio" value={standard.has(ratio) ? ratio : "custom"} disabled={!ready} onChange={event => choose(event.target.value)} sx={{ minWidth: 200 }} SelectProps={{ native: true }}>
        <option value="free">Free</option><option value="current">Current image</option><option value="1:1">Square</option><option value="screen">This screen</option>
        <optgroup label="Landscape">{landscape.map(value => <option key={value} value={value}>{value}</option>)}<option value="iso-landscape">ISO (A4, A3…)</option><option value="letter-landscape">US Letter</option></optgroup>
        <optgroup label="Portrait">{portrait.map(value => <option key={value} value={value}>{value}</option>)}<option value="iso-portrait">ISO (A4, A3…)</option><option value="letter-portrait">US Letter</option></optgroup>
        <optgroup label="Training dataset">{[["closeup","Face / close-up"],["portrait","Portrait"],["half_body","Half body"],["full_body","Full body"]].map(([key,label]) => <option key={key} value={`framing:${key}`}>{label}</option>)}</optgroup>
        {!standard.has(ratio) && <option value="custom">Custom ({ratio})</option>}
      </TextField>
      <FormControlLabel control={<Switch checked={advanced} onChange={event => onOptions({ advanced: event.target.checked })} />} label="Advanced settings" />
      <Button disabled={!ready} onClick={() => { onOptions({ ratio: "free" }); applied.current = "free"; apply(fitCrop(image, null), "free"); }}>Reset</Button>
    </Stack>
    {advanced && <Stack direction="row" useFlexGap flexWrap="wrap" spacing={1} sx={{ mt: 1 }} alignItems="start">
      <TextField size="small" label="Ratio W:H" value={ratioText} onChange={event => setRatioText(event.target.value)} onBlur={commitRatio} onKeyDown={event => { if (event.key === "Enter") commitRatio(); }} sx={{ width: 150 }} helperText="For example 4:5" />
      {(["width", "height", "x", "y"] as const).map(key => <TextField key={key} size="small" type="number" label={key === "width" ? "Width (px)" : key === "height" ? "Height (px)" : key.toUpperCase()} value={fields[key]} disabled={!ready} onFocus={() => { focused.current = key; }} onChange={event => setFields(old => ({ ...old, [key]: event.target.value }))} onBlur={() => { focused.current = null; commitField(key); }} onKeyDown={event => { if (event.key === "Enter") commitField(key); }} slotProps={{ htmlInput: { min: key === "x" || key === "y" ? 0 : 1, step: 1 } }} sx={{ width: 120 }} />)}
      <Button variant="outlined" disabled={!ready} onClick={commitRatio}>Apply crop</Button>
    </Stack>}
    {error && <Alert severity="error" sx={{ mt: 1 }}>{error}</Alert>}
  </Box>;
}
