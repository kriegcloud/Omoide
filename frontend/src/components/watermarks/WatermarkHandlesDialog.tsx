import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, Stack, TextField, Typography } from '@mui/material';
import { jsonBody, watermarkJson } from '../../services/watermarkOperations';
import { useDialogHotkeyScope, useHotkey } from '../../hotkeys/useHotkey';
import { startOperation } from '../../stores/optimisticOperations';
import { useListStore, useOptimisticCachedList, invalidateCachedLists, refreshCachedList } from '../../stores/useListStore';
import ConfirmDialog from '../ConfirmDialog';
interface Handle { id: number; display_text: string; match_count: number }
const key = 'watermarks-handles';
export default function WatermarkHandlesDialog({ onClose }: { onClose: () => void }) {
  const list = useOptimisticCachedList<Handle>(key, 'config');
  const [text, setText] = useState(''), [editing, setEditing] = useState<number | null>(null), [focused, setFocused] = useState<Handle | null>(null), [deleting, setDeleting] = useState<Handle | null>(null), [error, setError] = useState('');
  const ref = useDialogHotkeyScope(true);
  const fetchRows = useCallback(async () => ({ items: await watermarkJson<Handle[]>('/api/watermarks/handles'), next_cursor: null }), []);
  useEffect(() => { void useListStore.getState().fetchInitial(key, fetchRows); }, [fetchRows]);
  const write = (action: 'save' | 'delete', row?: Handle) => {
    const id = row?.id ?? editing ?? -Date.now(), remove = action === 'delete';
    try {
      startOperation({ label: remove ? 'Delete learned handle' : editing ? 'Rename learned handle' : 'Learn watermark handle', input: { display_text: text }, resources: [`config:${id}`], patches: [remove ? { kind: 'remove', key: `config:${id}`, query: key } : editing ? { kind: 'fields', key: `config:${id}`, query: key, values: { display_text: text } } : { kind: 'insert', key: `config:${id}`, query: key, item: { id, display_text: text, match_count: 0 } }],
        request: async (input, operationId) => watermarkJson(`/api/watermarks/handles${remove || editing ? `/${id}` : ''}`, remove ? { method: 'DELETE' } : jsonBody({ ...input, operation_id: operationId }, editing ? 'PATCH' : 'POST')),
        refresh: async () => { invalidateCachedLists(key); await refreshCachedList(key); },
      });
      setText(''); setEditing(null); setDeleting(null);
    } catch (cause) { setError(String(cause)); }
  };
  const save = () => { if (text.trim().length >= 3) write('save'); };
  const rename = () => { if (focused) { setEditing(focused.id); setText(focused.display_text); } };
  const options = { scope: 'dialog' as const, dialogRef: ref };
  useHotkey({ key: 's', ctrl: true }, save, { ...options, allowInInput: true, description: 'Save learned handle' });
  useHotkey({ key: 'n' }, () => { setEditing(null); setText(''); }, { ...options, description: 'New watermark handle' });
  useHotkey({ key: 'r' }, rename, { ...options, description: 'Rename focused watermark handle' });
  useHotkey({ key: 'Delete' }, () => setDeleting(focused), { ...options, description: 'Delete focused watermark handle', destructive: true });
  return <Dialog open ref={ref} onClose={onClose} fullWidth><DialogTitle>Learned watermark handles</DialogTitle><DialogContent><Stack spacing={2}>
    {(error || list?.error) && <Alert severity="error">{error || list?.error}</Alert>}
    <Typography color="text.secondary">Matching handles can be detected anywhere in an image. Focus a handle to rename or delete it with the keyboard.</Typography><TextField label="Handle" value={text} onChange={e => setText(e.target.value)} /><Button disabled={text.trim().length < 3} onClick={save}>{editing ? 'Save rename' : 'Add handle'}</Button>{list?.items.map(row => <Stack key={row.id} tabIndex={0} onFocus={() => setFocused(row)} direction="row" spacing={1} alignItems="center" sx={{ '&:focus-visible': { outline: '2px solid', outlineColor: 'primary.main' } }}><Typography sx={{ flex: 1 }}>{row.display_text} · {row.match_count} matches</Typography><Button onClick={() => { setEditing(row.id); setText(row.display_text); }}>Rename</Button><Button color="error" onClick={() => setDeleting(row)}>Delete</Button></Stack>)}</Stack>
    <ConfirmDialog open={!!deleting} title="Delete learned handle?" message={deleting?.display_text || ''} onClose={() => setDeleting(null)} onConfirm={() => { if (deleting) write('delete', deleting); }} />
  </DialogContent><DialogActions><Button onClick={onClose}>Close</Button></DialogActions></Dialog>;
}
