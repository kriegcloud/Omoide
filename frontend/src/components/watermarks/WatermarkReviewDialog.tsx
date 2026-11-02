import { useEffect, useState } from 'react';
import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, Stack, Switch, Typography } from '@mui/material';
import { useSearchParams } from 'react-router-dom';
import { API } from '../../config';
import { encodeFilePath } from '../../urlUtils';
import { useDialogHotkeyScope, useHotkey, useHotkeys } from '../../hotkeys/useHotkey';
import { watermarkJson, watermarkWrite, jsonBody, type WatermarkCandidate } from '../../services/watermarkOperations';
import WatermarkMaskEditor from './WatermarkMaskEditor';
import WatermarkComparison from './WatermarkComparison';

export default function WatermarkReviewDialog({ id, onClose, onStep, onAction }: { id: number; onClose: () => void; onStep: (delta: number) => void; onAction: (action: 'approve' | 'reject' | 'restore', rows: WatermarkCandidate[]) => void }) {
  const [item, setItem] = useState<WatermarkCandidate | null>(null), [original, setOriginal] = useState(''), [error, setError] = useState(''), [heldOriginal, setHeldOriginal] = useState(false), [revision, setRevision] = useState(0);
  const [params, setParams] = useSearchParams();
  const originalToggle = params.get('original') === '1', mask = params.get('mask') === '1';
  const showOriginal = heldOriginal || originalToggle;
  const option = (key: string, enabled: boolean) => setParams(previous => { const next = new URLSearchParams(previous); if (enabled) next.set(key, '1'); else next.delete(key); return next; }, { replace: true });
  const ref = useDialogHotkeyScope(true);
  const refresh = async () => { const candidate = await watermarkJson<WatermarkCandidate>(`/api/watermarks/${id}`); setItem(candidate); setRevision(x => x + 1); };
  useEffect(() => {
    let live = true; setItem(null); setOriginal(''); setError(''); setHeldOriginal(false);
    void watermarkJson<WatermarkCandidate>(`/api/watermarks/${id}`).then(async candidate => { const detail = await watermarkJson<{ media?: { path?: string }; path?: string }>(`/api/media/${candidate.media_id}`); const path = candidate.backup_path || detail.media?.path || detail.path; if (!path) throw new Error('Media file path is unavailable'); if (live) { setItem(candidate); setOriginal(`${API}/originals/${encodeFilePath(path)}`); } }).catch(e => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, [id]);
  useEffect(() => { const release = (event: KeyboardEvent) => { if (event.code === 'Space') setHeldOriginal(false); }; const blur = () => setHeldOriginal(false); window.addEventListener('keyup', release); window.addEventListener('blur', blur); return () => { window.removeEventListener('keyup', release); window.removeEventListener('blur', blur); }; }, []);
  const options = { scope: 'dialog' as const, dialogRef: ref };
  useHotkey({ key: ' ' }, () => setHeldOriginal(true), { ...options, description: 'Hold to show original' });
  useHotkey({ key: 'a' }, () => { if (item?.status === 'proposed') onAction('approve', [item]); }, { ...options, description: 'Approve watermark replacement' });
  useHotkey({ key: 'r' }, () => { if (item?.status === 'proposed') onAction('reject', [item]); }, { ...options, description: 'Reject watermark' });
  useHotkey({ key: 'u' }, () => { if (item?.backup_path) onAction('restore', [item]); }, { ...options, description: 'Restore original backup' });
  useHotkeys([{ key: 'ArrowRight' }, { key: 'j' }], () => onStep(1), { ...options, description: 'Next watermark' });
  useHotkeys([{ key: 'ArrowLeft' }, { key: 'k' }], () => onStep(-1), { ...options, description: 'Previous watermark' });
  useHotkey({ key: 'l' }, () => { const region = item?.regions[0]; if (region) watermarkWrite('Learn watermark handle', '/api/watermarks/handles', jsonBody({ display_text: region.text })); }, { ...options, description: 'Learn first detected watermark handle' });
  useHotkey({ key: 'm' }, () => option('mask', !mask), { ...options, description: 'Toggle mask editor' });
  useHotkey({ key: 'o' }, () => option('original', !originalToggle), { ...options, description: 'Toggle original comparison' });
  return <Dialog open ref={ref} onClose={onClose} maxWidth="lg" fullWidth><DialogTitle>Watermark review · {id}</DialogTitle><DialogContent>
    {error && <Alert severity="error">{error}</Alert>}{item?.error && <Alert severity="warning">{item.error}</Alert>}
    {item && original && <Stack spacing={2}>
      <Stack direction="row"><FormControlLabel control={<Switch checked={originalToggle} onChange={(_, checked) => option('original', checked)} />} label="Show original (hold Space)" /><FormControlLabel control={<Switch checked={mask} onChange={(_, checked) => option('mask', checked)} />} label="Mask editor" /></Stack>
      {mask ? <WatermarkMaskEditor id={id} originalUrl={original} onSaved={refresh} dialogRef={ref} editable={item.status === 'proposed'} /> : <WatermarkComparison original={original} preview={item.preview_path ? `${API}/api/watermarks/${id}/preview?v=${revision}` : undefined} showOriginal={showOriginal} />}
      {item.regions.map((region, index) => <Stack key={index} direction="row" spacing={2} alignItems="center"><Typography>{region.text}</Typography><Button onClick={() => watermarkWrite('Learn watermark handle', '/api/watermarks/handles', jsonBody({ display_text: region.text }))}>Learn this handle</Button></Stack>)}
    </Stack>}
  </DialogContent><DialogActions><Button onClick={() => onStep(-1)}>Previous</Button><Button onClick={() => onStep(1)}>Next</Button>{item?.status === 'proposed' && <><Button onClick={() => onAction('reject', [item])}>Reject</Button><Button onClick={() => onAction('approve', [item])}>Approve</Button></>}{item?.backup_path && <Button onClick={() => onAction('restore', [item])}>Restore original</Button>}<Button onClick={onClose}>Close</Button></DialogActions></Dialog>;
}
