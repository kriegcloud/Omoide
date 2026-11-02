import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Alert, Box, Slider } from '@mui/material';
/** Decode original without EXIF transforms: OCR preview/mask coordinates are raw raster. */
export default function WatermarkComparison({ original, preview, showOriginal }: { original: string; preview?: string; showOriginal: boolean }) {
  const ref = useRef<HTMLCanvasElement>(null), images = useRef<ImageBitmap[]>([]);
  const [params, setParams] = useSearchParams();
  const rawSplit = Number(params.get('split') ?? 50);
  const split = Number.isFinite(rawSplit) ? Math.min(100, Math.max(0, rawSplit)) : 50;
  const [error, setError] = useState(''), [loaded, setLoaded] = useState(0);
  useEffect(() => {
    let live = true; setError('');
    void Promise.all([original, preview].filter((url): url is string => !!url).map(async url => {
      const response = await fetch(url); if (!response.ok) throw new Error(`Comparison image unavailable (${response.status})`);
      return createImageBitmap(await response.blob(), { imageOrientation: 'none' });
    })).then(bitmaps => { if (!live) { bitmaps.forEach(image => image.close()); return; } images.current = bitmaps; setLoaded(x => x + 1); }).catch(e => { if (live) setError(String(e)); });
    return () => { live = false; images.current.forEach(image => image.close()); images.current = []; };
  }, [original, preview]);
  useEffect(() => {
    const canvas = ref.current, [before, after] = images.current; if (!canvas || !before) return;
    canvas.width = before.width; canvas.height = before.height; const ctx = canvas.getContext('2d')!;
    ctx.drawImage(before, 0, 0);
    if (!showOriginal && after) { ctx.save(); ctx.beginPath(); ctx.rect(before.width * split / 100, 0, before.width, before.height); ctx.clip(); ctx.drawImage(after, 0, 0, before.width, before.height); ctx.restore(); }
  }, [loaded, split, showOriginal]);
  return <>{error && <Alert severity="error">{error}</Alert>}<Box component="canvas" ref={ref} aria-label="Before and after watermark comparison, original on left" sx={{ display: 'block', maxWidth: '100%', maxHeight: '60vh', width: 'auto', height: 'auto', mx: 'auto' }} /><Slider aria-label="Before and after split" value={split} onChange={(_, value) => setParams(previous => { const next = new URLSearchParams(previous); next.set('split', String(value)); return next; }, { replace: true })} /></>;
}
