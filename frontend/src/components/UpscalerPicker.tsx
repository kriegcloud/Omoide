import { useEffect, useState } from "react";
import { Alert, Button, Checkbox, FormControl, FormControlLabel, FormHelperText, InputLabel, MenuItem, Select, Stack, TextField, Typography } from "@mui/material";
import { listUpscalers, type UpscalerProvider, type UpscaleSelection } from "../services/repairs";

export type { UpscaleSelection } from "../services/repairs";
export const defaultUpscaleSelection = (provider = "comfy:remacri-2x"): UpscaleSelection => ({ provider, params: {}, hostedConsent: false });
interface Props {
  value: UpscaleSelection;
  onChange: (selection: UpscaleSelection) => void;
  onValidityChange: (valid: boolean) => void;
  disabled?: boolean;
}

/** Registry-driven form shared by card repair and the image editor. */
export default function UpscalerPicker({ value, onChange, onValidityChange, disabled }: Props) {
  const [providers, setProviders] = useState<UpscalerProvider[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let current = true;
    setError(null);
    void listUpscalers().then(rows => { if (current) setProviders(rows); }).catch(reason => {
      if (current) setError(reason instanceof Error ? reason.message : "Cannot load upscalers");
    });
    return () => { current = false; };
  }, [retry]);
  const selected = providers.find(provider => provider.id === value.provider);
  const validParams = selected && Object.entries(selected.params).every(([name, spec]) => {
    const v = value.params[name] ?? spec.default;
    if (spec.choices && !spec.choices.includes(v)) return false;
    if (name.endsWith("_edge_size") && v === 0) return false;
    if (spec.type === "string") return typeof v === "string" && (spec.max_length == null || v.length <= spec.max_length);
    return typeof v === "number" && Number.isFinite(v) && Number.isInteger(v)
      && (spec.min == null || v >= spec.min) && (spec.max == null || v <= spec.max);
  });
  const valid = Boolean(selected?.available && !error && validParams && (selected.kind !== "hosted" || value.hostedConsent));
  useEffect(() => { onValidityChange(valid); }, [valid, onValidityChange]);
  const changeParam = (name: string, v: string | number) => onChange({ ...value, params: { ...value.params, [name]: v } });
  return <Stack spacing={2}>
    {error && <Alert severity="error" action={<Button onClick={() => setRetry(n => n + 1)}>Retry</Button>}>{error}</Alert>}
    {providers.length > 0 && !selected && <Alert severity="warning">Unknown upscaler “{value.provider}”. Choose an available upscaler.</Alert>}
    <FormControl fullWidth disabled={disabled || !providers.length}>
      <InputLabel id="upscaler-provider-label">Upscaler</InputLabel>
      <Select labelId="upscaler-provider-label" label="Upscaler" value={selected ? value.provider : ""}
        onChange={event => onChange(defaultUpscaleSelection(event.target.value))}>
        {providers.map(provider => <MenuItem key={provider.id} value={provider.id} disabled={!provider.available} sx={{ whiteSpace: "normal" }}>
          {provider.label} · {provider.kind === "hosted" ? "Hosted, paid" : "Local"}{!provider.available ? ` — ${provider.reason || "Unavailable"}` : ""}
        </MenuItem>)}
      </Select>
      <FormHelperText>{!providers.length && !error ? "Loading upscalers…" : "Upscaling creates a new image and keeps the source."}</FormHelperText>
    </FormControl>
    {selected && <>
      <Typography variant="body2" color="text.secondary">{selected.fidelity_note}</Typography>
      {!selected.available && <Alert severity="warning">{selected.reason || "This upscaler is unavailable."}</Alert>}
      {selected.kind === "hosted" && <Alert severity="info">Long edge overrides short edge; either overrides scale. Use -1 to leave an edge size unset.</Alert>}
      {Object.entries(selected.params).map(([name, spec]) => {
        const label = name === "scale" ? "Scale" : name.replaceAll("_", " ").replace(/^./, c => c.toUpperCase());
        const v = value.params[name] ?? spec.default;
        return spec.choices ? <TextField key={name} select label={label} value={v} disabled={disabled} onChange={event => changeParam(name, typeof spec.default === "number" ? Number(event.target.value) : event.target.value)}>
          {spec.choices.map(choice => <MenuItem key={choice} value={choice}>{name === "scale" ? `${choice}×` : choice}</MenuItem>)}
        </TextField> : <TextField key={name} label={label} type={spec.type === "string" ? "text" : "number"} value={v} disabled={disabled}
          onChange={event => changeParam(name, spec.type === "string" ? event.target.value : event.target.value === "" ? "" : Number(event.target.value))}
          helperText={name.endsWith("_edge_size") ? "-1 (unset), or a positive pixel size; 0 is invalid" : undefined}
          slotProps={{ htmlInput: { min: spec.min, max: spec.max, maxLength: spec.max_length, step: 1 } }} />;
      })}
      {selected.warning && <Alert severity="warning">{selected.warning}</Alert>}
      {selected.id === "comfy:seedvr2-sharp" && Number(value.params.scale ?? selected.params.scale?.default) === 4 && <Alert severity="warning">4× SeedVR2 requires substantially more memory and may invent more detail.</Alert>}
      {selected.kind === "hosted" && <Alert severity="warning">
        <Typography variant="body2">This paid service uploads the selected image{ " " }to HYPIR (suppixel.ai). Generative restoration may change details.</Typography>
        <FormControlLabel control={<Checkbox disabled={disabled} checked={value.hostedConsent} onChange={event => onChange({ ...value, hostedConsent: event.target.checked })} />} label="Send this image to the hosted service for this run" />
      </Alert>}
    </>}
  </Stack>;
}
