import { useEffect, useRef, useState, type RefObject } from 'react';
import { Alert, Box, Button, Slider, Stack, ToggleButton, ToggleButtonGroup } from '@mui/material';
import { useSearchParams } from 'react-router-dom';
import { API } from '../../config';
import { createRasterMask, maskPngBase64, rasterPoint } from '../../utils/watermarkMask';
import { jsonBody, watermarkWrite } from '../../services/watermarkOperations';
import { useHotkey } from '../../hotkeys/useHotkey';

export default function WatermarkMaskEditor({ id, originalUrl, onSaved, editable, dialogRef }: { id: number; originalUrl: string; onSaved: () => Promise<void>; editable: boolean; dialogRef: RefObject<HTMLElement | null> }) {
  const canvas = useRef<HTMLCanvasElement>(null), mask = useRef<HTMLCanvasElement | null>(null), source = useRef<ImageBitmap | null>(null);
  const previous = useRef<{ x: number; y: number } | null>(null);
  const [params, setParams] = useSearchParams();
  const mode = params.get('brush') === 'erase' ? 'erase' : 'paint';
  const rawSize = Number(params.get('brush_size') ?? 20);
  const size = Number.isFinite(rawSize) ? Math.max(1, Math.min(150, rawSize)) : 20;
  const setMode = (value: 'paint' | 'erase') => setParams(previous => { const next = new URLSearchParams(previous); next.set('brush', value); return next; }, { replace: true });
  const setSize = (value: number | ((current: number) => number)) => setParams(previous => { const next = new URLSearchParams(previous); next.set('brush_size', String(typeof value === 'function' ? value(size) : value)); return next; }, { replace: true });
  const [ready, setReady] = useState(false), [error, setError] = useState('');
  const render = () => {
    const target = canvas.current, base = source.current, binary = mask.current;
    if (!target || !base || !binary) return;
    const ctx = target.getContext('2d')!; ctx.drawImage(base, 0, 0);
    const overlay = document.createElement('canvas'); overlay.width = binary.width; overlay.height = binary.height;
    const context = overlay.getContext('2d')!, data = binary.getContext('2d')!.getImageData(0, 0, binary.width, binary.height);
    for (let i = 0; i < data.data.length; i += 4) { const alpha = data.data[i] ? 110 : 0; data.data[i] = 255; data.data[i + 1] = 0; data.data[i + 2] = 0; data.data[i + 3] = alpha; }
    context.putImageData(data, 0, 0); ctx.drawImage(overlay, 0, 0);
  };
  useEffect(() => {
    let live = true; setReady(false);
    void Promise.all([fetch(originalUrl).then(r => { if (!r.ok) throw new Error('Original unavailable'); return r.blob(); }).then(b => createImageBitmap(b, { imageOrientation: 'none' })), fetch(`${API}/api/watermarks/${id}/mask`).then(r => { if (!r.ok) throw new Error('Mask unavailable'); return r.blob(); }).then(b => createImageBitmap(b, { imageOrientation: 'none' }))]).then(([image, initial]) => {
      if (!live) { image.close(); initial.close(); return; }
      if (image.width !== initial.width || image.height !== initial.height) { image.close(); initial.close(); throw new Error('Mask and source raster dimensions differ'); }
      source.current = image; const binary = createRasterMask(image, initial); initial.close(); mask.current = binary;
      if (canvas.current) { canvas.current.width = image.width; canvas.current.height = image.height; } render(); setReady(true);
    }).catch(e => { if (live) setError(String(e)); });
    return () => { live = false; source.current?.close(); source.current = null; };
  }, [id, originalUrl]);
  const save = () => { if (mask.current && ready && editable) watermarkWrite('Save watermark mask', `/api/watermarks/${id}/mask`, jsonBody({ mask_png_base64: maskPngBase64(mask.current) }, 'PATCH'), onSaved); };
  useHotkey({ key: 's', ctrl: true }, save, { scope: 'dialog', dialogRef, enabled: ready && editable, description: 'Save watermark mask' });
  useHotkey({ key: 'b' }, () => setMode('paint'), { scope: 'dialog', dialogRef, description: 'Paint mask' });
  useHotkey({ key: 'f' }, () => { canvas.current?.scrollIntoView({ block: 'nearest' }); render(); }, { scope: 'dialog', dialogRef, description: 'Fit watermark mask in view' });
  useHotkey({ key: '[' }, () => setSize(value => Math.max(1, value - 5)), { scope: 'dialog', dialogRef, description: 'Smaller watermark brush' });
  useHotkey({ key: ']' }, () => setSize(value => Math.min(150, value + 5)), { scope: 'dialog', dialogRef, description: 'Larger watermark brush' });
  useHotkey({ key: 'e' }, () => setMode('erase'), { scope: 'dialog', dialogRef, description: 'Erase mask' });
  const draw = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!editable || !ready || !mask.current || !canvas.current) return;
    const p = rasterPoint(event.clientX, event.clientY, canvas.current.getBoundingClientRect(), mask.current.width, mask.current.height), ctx = mask.current.getContext('2d')!;
    ctx.strokeStyle = mode === 'paint' ? '#ffffff' : '#000000'; ctx.fillStyle = ctx.strokeStyle; ctx.lineWidth = size; ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(previous.current?.x ?? p.x, previous.current?.y ?? p.y); ctx.lineTo(p.x, p.y); ctx.stroke(); ctx.beginPath(); ctx.arc(p.x, p.y, size / 2, 0, Math.PI * 2); ctx.fill(); previous.current = p; render();
  };
  return <Stack spacing={1}>
    {error && <Alert severity="error">{error}</Alert>}
    <Stack direction="row" spacing={1} alignItems="center"><ToggleButtonGroup exclusive value={mode} onChange={(_, value) => value && setMode(value)}><ToggleButton value="paint">Paint</ToggleButton><ToggleButton value="erase">Erase</ToggleButton></ToggleButtonGroup><Slider aria-label="Brush size in source pixels" min={1} max={150} value={size} onChange={(_, value) => setSize(value as number)} valueLabelDisplay="auto" sx={{ width: 160 }} /><Button onClick={() => { canvas.current?.scrollIntoView({ block: 'nearest' }); render(); }}>Fit mask in view</Button><Button disabled={!ready || !editable} onClick={save}>Save mask</Button></Stack>
    <Box component="canvas" ref={canvas} aria-label="Watermark brush mask, source raster coordinates" sx={{ display: 'block', maxWidth: '100%', maxHeight: '60vh', width: 'auto', height: 'auto', touchAction: 'none', cursor: 'crosshair', alignSelf: 'center' }} onPointerDown={event => { event.currentTarget.setPointerCapture(event.pointerId); previous.current = null; draw(event); }} onPointerMove={event => { if (event.buttons === 1) draw(event); }} onPointerUp={() => { previous.current = null; }} onPointerCancel={() => { previous.current = null; }} />
  </Stack>;
}
