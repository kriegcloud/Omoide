import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Box, Button, Menu, MenuItem, Stack, Tab, Tabs, TextField, Typography } from '@mui/material';
import { useSearchParams } from 'react-router-dom';
import { API } from '../config';
import { useTaskCompletionVersion } from '../TaskEventsContext';
import GalleryPage from '../components/GalleryPage';
import Gallery from '../components/Gallery';
import SelectableTileFrame from '../components/SelectableTileFrame';
import ConfirmDialog from '../components/ConfirmDialog';
import WatermarkReviewDialog from '../components/watermarks/WatermarkReviewDialog';
import WatermarkHandlesDialog from '../components/watermarks/WatermarkHandlesDialog';
import { useSelection } from '../context/SelectionContext';
import { useGridSelection } from '../hooks/useMarqueeSelection';
import { useHotkey } from '../hotkeys/useHotkey';
import { queryKey } from '../stores/queryKeys';
import { useListStore, useListInvalidation, useOptimisticCachedList, refreshCachedList } from '../stores/useListStore';
import { changeWatermarks, jsonBody, watermarkJson, watermarkWrite, type WatermarkCandidate } from '../services/watermarkOperations';
import type { CursorPage } from '../types';

const statuses = ['proposed', 'applied', 'rejected', 'failed'];
export default function WatermarksPage() {
  const globalSelection = useSelection();
  const taskVersion = useTaskCompletionVersion(['detect_watermarks']);
  const [actionError, setActionError] = useState('');
  const [params, setParams] = useSearchParams();
  const status = statuses.includes(params.get('status') || '') ? params.get('status')! : 'proposed', folder = params.get('folder') || '';
  const item = Number(params.get('item')) || null;
  const key = queryKey('watermarks', { status, folder });
  const list = useOptimisticCachedList<WatermarkCandidate>(key, 'watermark');
  const [selected, setSelected] = useState<Set<number>>(new Set()), [localSelecting, setSelecting] = useState(false), [handles, setHandles] = useState(false), [menu, setMenu] = useState<HTMLElement | null>(null), [purge, setPurge] = useState(false), [days, setDays] = useState('30');
  const [confirm, setConfirm] = useState<{ action: 'approve' | 'restore'; rows: WatermarkCandidate[] } | null>(null);
  const selecting = localSelecting || globalSelection.isSelecting;
  const change = (action: 'approve' | 'reject' | 'restore', targets: WatermarkCandidate[]) => { try { changeWatermarks(action, targets); } catch (error) { setActionError(String(error)); } };
  const container = useRef<HTMLDivElement>(null);
  const update = (name: string, value: string | null) => setParams(previous => { const next = new URLSearchParams(previous); if (value) next.set(name, value); else next.delete(name); if (name !== 'item') next.delete('item'); return next; });
  const fetchPage = useCallback(async (cursor?: string): Promise<CursorPage<WatermarkCandidate>> => {
    const search = new URLSearchParams({ status, limit: '50' }); if (folder) search.set('folder', folder); if (cursor) search.set('cursor', cursor);
    const result = await watermarkJson<{ items: WatermarkCandidate[]; next_cursor: string | number | null }>(`/api/watermarks?${search}`);
    return { items: result.items, next_cursor: result.next_cursor === null ? null : String(result.next_cursor) };
  }, [status, folder]);
  useEffect(() => { setSelected(new Set()); void useListStore.getState().fetchInitial(key, fetchPage); }, [key, fetchPage]);
  useListInvalidation(key);
  useEffect(() => { if (taskVersion) void refreshCachedList(key).catch(error => setActionError(String(error))); }, [key, taskVersion]);
  const rows = list?.items ?? [];
  const selection = useGridSelection({ listKey: key, containerRef: container, orderedIds: rows.map(row => row.id), loadedCount: rows.length, hasMore: list?.hasMore, selecting, onEnterSelection: () => setSelecting(true), onExitSelection: () => setSelecting(false), selectedIds: selected, onSelectionChange: setSelected });
  const act = (action: 'approve' | 'reject' | 'restore', targets: WatermarkCandidate[]) => { if (!targets.length) return; if (action !== 'reject') setConfirm({ action, rows: structuredClone(targets) }); else { change(action, targets); setSelected(new Set()); update('item', null); } };
  const chosen = () => rows.filter(row => selected.has(row.id));
  const detect = () => { const search = new URLSearchParams({ scope: 'pending' }); if (folder) search.set('folder', folder); watermarkWrite('Detect watermarks', `/api/watermarks/detect?${search}`, jsonBody({})); };
  useHotkey({ key: 'a' }, () => act('approve', chosen()), { scope: 'page', enabled: !item && status === 'proposed', description: 'Approve selected watermarks' });
  useHotkey({ key: 'r' }, () => act('reject', chosen()), { scope: 'page', enabled: !item && status === 'proposed', description: 'Reject selected watermarks' });
  useHotkey({ key: 'u' }, () => act('restore', chosen()), { scope: 'page', enabled: !item && status === 'applied', description: 'Restore selected originals' });
  useHotkey({ key: 'd' }, detect, { scope: 'page', enabled: !item, description: 'Detect watermarks in this folder' });
  useHotkey({ key: 'h' }, () => setHandles(true), { scope: 'page', enabled: !item, description: 'Manage learned watermark handles' });
  useHotkey({ key: 'p', shift: true }, () => setPurge(true), { scope: 'page', enabled: !item, description: 'Review backup purge', destructive: true });
  const step = (delta: number) => { const index = rows.findIndex(row => row.id === item); const next = rows[index + delta]; if (next) update('item', String(next.id)); };
  return <GalleryPage title="Watermarks" count={rows.length} countLabel="loaded candidates" tabs={<Tabs value={status} onChange={(_, value) => update('status', value)}>{statuses.map(value => <Tab key={value} value={value} label={value} />)}</Tabs>}
    filters={<TextField label="Folder" size="small" value={folder} onChange={event => update('folder', event.target.value)} />}
    actions={<><Button onClick={detect}>Detect watermarks</Button><Button onClick={() => setHandles(true)}>Handles</Button><Button onClick={event => setMenu(event.currentTarget)}>More</Button></>}
    selection={<Stack direction="row" spacing={1} sx={{ mb: 2 }}><Button onClick={() => setSelecting(x => !x)}>{selecting ? 'Done selecting' : 'Select'}</Button>{selecting && <><Button onClick={() => setSelected(new Set(rows.map(row => row.id)))}>Select all loaded</Button><Typography>{selected.size} selected</Typography>{status === 'proposed' && <><Button disabled={!selected.size} onClick={() => act('approve', chosen())}>Approve selected</Button><Button disabled={!selected.size} onClick={() => act('reject', chosen())}>Reject selected</Button></>}{status === 'applied' && <Button disabled={!selected.size} onClick={() => act('restore', chosen())}>Restore selected</Button>}</>}</Stack>}>
    {actionError && <Alert severity="error" onClose={() => setActionError('')}>{actionError}</Alert>}
    {list?.error && <Alert severity="error" action={<Button onClick={() => { void refreshCachedList(key); }}>Retry</Button>}>{list.error}</Alert>}
    {!rows.length && !list?.isLoading && <Typography color="text.secondary">No {status} watermark candidates in this folder.</Typography>}
    <Box ref={container} sx={{ position: 'relative' }}><Gallery>{rows.map(row => <SelectableTileFrame key={row.id} id={row.id} selected={selected.has(row.id)} selecting={selecting} keyboardReview onSelectionClick={selection.onItemClick} onOpen={() => update('item', String(row.id))} footer={<Box sx={{ p: 1 }}><Typography variant="body2">{row.regions.map(region => region.text).join(' · ') || 'No detected text'}</Typography></Box>}><Box component="img" loading="lazy" src={`${API}/api/watermarks/${row.id}/preview`} alt={row.regions.map(region => region.text).join(', ')} sx={{ width: '100%', display: 'block' }} /></SelectableTileFrame>)}</Gallery>{selection.marqueeRect && <Box sx={{ position: 'fixed', ...selection.marqueeRect, pointerEvents: 'none', border: '1px solid', borderColor: 'primary.main', bgcolor: 'action.selected', zIndex: 1000 }} />}</Box>
    {list?.hasMore && <Button disabled={list.isLoading} onClick={() => { void useListStore.getState().loadMore(key, fetchPage); }}>Load more candidates</Button>}
    {item && <WatermarkReviewDialog key={item} id={item} onClose={() => update('item', null)} onStep={step} onAction={act} />}
    {handles && <WatermarkHandlesDialog onClose={() => setHandles(false)} />}
    <Menu anchorEl={menu} open={!!menu} onClose={() => setMenu(null)}><MenuItem onClick={() => { setMenu(null); setPurge(true); }}>Purge old backups…</MenuItem></Menu>
    <ConfirmDialog open={!!confirm} title={confirm?.action === 'approve' ? 'Replace originals with cleaned images?' : 'Restore original files?'} message={`${confirm?.rows.length ?? 0} candidate(s). ${confirm?.action === 'approve' ? 'Original bytes will be kept in backups for Restore.' : 'The cleaned copies will be replaced with their exact original bytes.'}`} onClose={() => setConfirm(null)} onConfirm={() => { if (confirm) change(confirm.action, confirm.rows); setConfirm(null); setSelected(new Set()); update('item', null); }} confirmLabel={confirm?.action === 'approve' ? 'Approve replacement' : 'Restore originals'} />
    <ConfirmDialog open={purge} title="Permanently purge old backups?" message={<Stack spacing={2}><Typography>Purged originals cannot be restored. Only backups older than this many days will be removed.</Typography><TextField label="Older than days" type="number" value={days} onChange={event => setDays(event.target.value)} inputProps={{ min: 1, step: 1 }} /></Stack>} confirmDisabled={!Number.isInteger(Number(days)) || Number(days) <= 0} confirmLabel="Purge backups" onClose={() => setPurge(false)} onConfirm={() => { if (Number.isInteger(Number(days)) && Number(days) > 0) watermarkWrite('Purge watermark backups', `/api/watermarks/backups/purge?older_than_days=${Number(days)}`, jsonBody({})); setPurge(false); }} />
  </GalleryPage>;
}
