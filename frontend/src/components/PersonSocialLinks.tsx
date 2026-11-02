import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import AddIcon from '@mui/icons-material/Add';
import MoreHorizIcon from '@mui/icons-material/MoreHoriz';
import DragIndicatorIcon from '@mui/icons-material/DragIndicator';
import { Alert, Box, Button, CircularProgress, IconButton, Link, Menu, MenuItem, Popover, Snackbar, Stack, TextField, Typography } from '@mui/material';
import config from '../config';
import { addSocialLink, deleteSocialLink, editSocialLink, getSocialBrand, getSocialLinks, previewSocialLink, reorderSocialLinks, updateSocialBrand } from '../services/personActions';
import { groupSocialLinks } from '../services/socialLinkGroups';
import { socialLinksQuery, startSocialLinkOperation } from '../services/socialLinkOperations';
import { acknowledgeQuery, operationVersion, optimisticOperations, projectEntity, refreshOperation } from '../stores/optimisticOperations';
import { useUndo } from '../context/UndoContext';
import type { SocialLink, SocialLinkPreview } from '../types';
import ConfirmDialog from './ConfirmDialog';
import { SocialBrandIcon } from './SocialBrandIcon';

const COLLAPSED_COUNT = 4;
export function PersonSocialLinks({ personId, initialLinks }: { personId: number; initialLinks: SocialLink[] }) {
  const [base, setBase] = useState(initialLinks);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState<{ message: string; uncertain: boolean } | null>(null);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [editing, setEditing] = useState<SocialLink | null>(null);
  const [url, setUrl] = useState('');
  const [label, setLabel] = useState('');
  const [preview, setPreview] = useState<SocialLinkPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [menu, setMenu] = useState<{ anchor: HTMLElement; link: SocialLink } | null>(null);
  const [deleting, setDeleting] = useState<SocialLink | null>(null);
  const [dragged, setDragged] = useState<number | null>(null);
  const records = useSyncExternalStore(optimisticOperations.subscribe, optimisticOperations.getSnapshot, optimisticOperations.getSnapshot);
  const { push } = useUndo();
  const query = socialLinksQuery(personId);
  const links = projectEntity(`person:${personId}`, { social_links: base }, query)?.social_links ?? [];
  const editingLink = links.find(link => link.id === editing?.id) ?? editing;
  const ordered = [...links].sort((a,b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));
  const pending = records.some(record => record.resources.includes(`person:${personId}`) && ['applied', 'pending', 'unknown'].includes(record.state));
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const started = operationVersion();
    const result = await getSocialLinks(personId, signal);
    setBase(result); acknowledgeQuery(socialLinksQuery(personId), started);
  }, [personId]);
  useEffect(() => {
    const controller = new AbortController();
    setBase(initialLinks); setLoading(true); setError(''); setExpanded(false); setAnchor(null);
    void refresh(controller.signal).catch(e => { if (e.name !== 'AbortError') setError(e.message); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
    // A server refresh owns base after mount; parent detail snapshots can be older.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [personId, refresh]);
  useEffect(() => {
    if (!anchor || !url.trim()) { setPreview(null); setPreviewing(false); return; }
    const controller = new AbortController(); setPreview(null); setPreviewing(true);
    const timer = window.setTimeout(() => { void previewSocialLink(url.trim(), controller.signal).then(result => { if (!controller.signal.aborted) { setPreview(result); setError(''); } }).catch(e => { if (!controller.signal.aborted) setError(e.message); }).finally(() => { if (!controller.signal.aborted) setPreviewing(false); }); }, 350);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [anchor, url]);
  const pendingDomains = [...new Set(links.filter(link => link.brand?.status === 'pending').map(link => link.brand_domain).filter((domain): domain is string => !!domain))].join('|');
  useEffect(() => {
    if (!pendingDomains) return;
    const controller = new AbortController();
    const timer = window.setInterval(() => { void Promise.all(pendingDomains.split('|').map(domain => getSocialBrand(domain, controller.signal))).then(brands => {
      if (!controller.signal.aborted) setBase(current => current.map(link => ({ ...link, brand: brands.find(brand => brand.domain === link.brand_domain) ?? link.brand })));
    }).catch(() => { /* A transient icon lookup keeps the generic fallback. */ }); }, 2500);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [pendingDomains]);
  const allGroups = groupSocialLinks(links);
  const groups = expanded ? allGroups : allGroups.slice(0, COLLAPSED_COUNT);
  const uncertainOperations = records.filter(record => record.resources.includes(`person:${personId}`) && record.state === 'unknown');
  function openEditor(target: HTMLElement, link: SocialLink | null = null) {
    setEditing(link); setUrl(link?.normalized_url || link?.url || ''); setLabel(link?.label || ''); setError(''); setPreview(null); setAnchor(target); setMenu(null);
  }
  function mutate(title: string, next: SocialLink[], request: () => Promise<unknown>, matches?: (rows: SocialLink[]) => boolean) {
    setError('');
    const handle = startSocialLinkOperation({ personId, label: title, links: next, request,
      refresh: () => refresh(),
      lookup: matches ? async id => {
        const rows = await getSocialLinks(personId);
        return { operation_id: id, state: matches(rows) ? 'succeeded' : 'unknown', applied: matches(rows) ? [`person:${personId}`] : [], unknown: matches(rows) ? [] : [`person:${personId}`], skipped: [], failed: [] };
      } : undefined,
    });
    void handle.done.then(result => {
      if (result.state === 'rejected') setFeedback({ message: result.error || `${title} failed. Your previous links were restored.`, uncertain: false });
      else if (result.state === 'unknown') setFeedback({ message: `${title}: outcome uncertain. Check status before making another change.`, uncertain: true });
    });
    return handle;
  }
  function save() {
    if (!preview || pending) return;
    const data = { url: preview.normalized_url, label: label.trim() || null };
    const optimistic: SocialLink = { ...preview, ...data, id: editing?.id ?? -Date.now(), created_at: editing?.created_at ?? new Date().toISOString(), sort_order: editing?.sort_order ?? links.length };
    const next = editing ? links.map(link => link.id === editing.id ? optimistic : link) : [...links, optimistic];
    mutate(editing ? 'Edit social link' : 'Add social link', next,
      () => editing ? editSocialLink(personId, editing.id, data) : addSocialLink(personId, data),
      rows => rows.some(link => (!editing || link.id === editing.id) && link.normalized_url === data.url && (link.label || null) === data.label));
    setAnchor(null);
  }
  function reorder(fromId: number, toId: number) {
    const next = [...ordered]; const from = next.findIndex(link => link.id === fromId), to = next.findIndex(link => link.id === toId);
    if (pending || from < 0 || to < 0 || from === to) return;
    next.splice(to, 0, next.splice(from, 1)[0]); const sorted = next.map((link,index) => ({ ...link, sort_order: index }));
    const ids = sorted.map(link => link.id);
    mutate('Reorder social links', sorted, () => reorderSocialLinks(personId, ids), rows => [...rows].sort((a,b)=>(a.sort_order ?? 0)-(b.sort_order ?? 0)).map(link=>link.id).join(',') === ids.join(','));
  }
  function remove() {
    if (!deleting || pending) return;
    const removed = deleting;
    const operation = mutate('Delete social link', links.filter(link => link.id !== removed.id), () => deleteSocialLink(personId, removed.id), rows => !rows.some(link=>link.id===removed.id));
    push({ label: 'Social link deleted', operationId: operation.id, undo: async () => {
      const current = await getSocialLinks(personId);
      const restored = mutate('Restore social link', [...current, removed], () => addSocialLink(personId, { url: removed.normalized_url || removed.url, label: removed.label, sort_order: removed.sort_order }), rows => rows.some(link => link.normalized_url === (removed.normalized_url || removed.url)));
      const result = await restored.done;
      if (result.state !== 'committed') throw new Error(result.state === 'unknown' ? 'Restore outcome uncertain. Check status before trying again.' : result.error || 'Could not restore link');
    } }); setDeleting(null);
  }
  function changeIcon(file?: File) {
    if (!editing?.brand_domain || pending) return;
    const domain = editing.brand_domain;
    const before = editingLink?.brand;
    mutate(file ? 'Upload site icon' : 'Retry site icon', links, async () => { await updateSocialBrand(domain, file); }, rows => rows.some(link => link.brand_domain === domain && !!link.brand && (file ? link.brand.source === 'manual' && link.brand.fetched_at !== before?.fetched_at : link.brand.status !== before?.status || link.brand.fetched_at !== before?.fetched_at)));
  }
  return <Box sx={{ mt: 1.5, minWidth: 0 }} aria-label="Social links">
    {loading && <Typography variant="caption" color="text.secondary">Loading links…</Typography>}
    {!loading && !links.length && <Typography variant="body2" color="text.secondary">No social links yet.</Typography>}
    <Stack spacing={0.5}>
      {groups.map(group => <Stack key={group.key} direction={{ xs: 'column', sm: 'row' }} spacing={{ xs: 0, sm: 1.5 }} alignItems={{ sm: 'baseline' }}>
        <Typography variant="body2" color="text.secondary" sx={{ minWidth: 100, pt: 0.75 }}>{group.name}</Typography>
        <Stack direction="row" useFlexGap flexWrap="wrap" spacing={0.5} sx={{ minWidth: 0 }}>
          {group.links.map(link => <Stack key={link.id} className="social-link-row" direction="row" alignItems="center" spacing={0.5}
            draggable={!config.PRESENTATION_MODE && !pending} onDragStart={() => setDragged(link.id)} onDragEnd={() => setDragged(null)}
            onDragOver={event => { if (dragged !== null) event.preventDefault(); }} onDrop={event => { event.preventDefault(); if (dragged !== null) reorder(dragged, link.id); setDragged(null); }}
            sx={{ py: 0.25, pl: 0.5, minWidth: 0, maxWidth: '100%', borderRadius: 1, '&:hover': { bgcolor: 'action.hover' } }}>
            {!config.PRESENTATION_MODE && <DragIndicatorIcon sx={{ fontSize: 14, color: 'text.disabled', cursor: pending ? 'default' : 'grab' }} />}
            <SocialBrandIcon link={link} />
            <Link href={link.normalized_url || link.url} target="_blank" rel="noopener noreferrer" color="text.primary" underline="hover" sx={{ fontSize: '0.875rem', overflowWrap: 'anywhere', textUnderlineOffset: '0.2em' }}>
              {link.label || (link.platform === 'other' ? link.handle || link.brand_domain : `@${link.handle.replace(/^@/, '')}`)}
            </Link>
            {!config.PRESENTATION_MODE && <IconButton size="small" aria-label={`Actions for ${link.label || link.handle}`} disabled={pending} onClick={event => setMenu({ anchor: event.currentTarget, link })}><MoreHorizIcon fontSize="small" /></IconButton>}
          </Stack>)}
        </Stack>
      </Stack>)}
    </Stack>
    <Stack direction="row" spacing={1} sx={{ mt: 0.5 }}>
      {allGroups.length > COLLAPSED_COUNT && <Button size="small" onClick={() => setExpanded(value => !value)}>{expanded ? 'Show fewer' : `Show all (${links.length})`}</Button>}
      {!config.PRESENTATION_MODE && <Button size="small" startIcon={<AddIcon />} disabled={pending} onClick={event => openEditor(event.currentTarget)}>Add link</Button>}
      {uncertainOperations.length > 0 && <Button size="small" onClick={() => { void Promise.all(uncertainOperations.map(record => refreshOperation(record.id))).catch(() => setFeedback({ message: 'Could not check status. Try again when the connection is available.', uncertain: true })); }}>Check status</Button>}
      {pending && <Typography variant="caption" color="text.secondary" role="status" sx={{ alignSelf: 'center' }}>Saving or checking outcome…</Typography>}
    </Stack>
    {error && <Alert severity="error" sx={{ mt: 1 }} onClose={() => setError('')}>{error}</Alert>}
    <Menu anchorEl={menu?.anchor} open={!!menu} onClose={() => setMenu(null)}>
      <MenuItem onClick={() => { if (menu) openEditor(menu.anchor, menu.link); }}>Edit</MenuItem>
      <MenuItem onClick={() => { if (menu) void navigator.clipboard.writeText(menu.link.normalized_url || menu.link.url).catch(() => setError('Could not copy link. Open it and copy the address.')); setMenu(null); }}>Copy link</MenuItem>
      <MenuItem disabled={!menu || ordered[0]?.id === menu.link.id} onClick={() => { if (menu) { const index=ordered.findIndex(link=>link.id===menu.link.id); reorder(menu.link.id, ordered[index-1].id); } setMenu(null); }}>Move earlier</MenuItem>
      <MenuItem disabled={!menu || ordered.at(-1)?.id === menu.link.id} onClick={() => { if (menu) { const index=ordered.findIndex(link=>link.id===menu.link.id); reorder(menu.link.id, ordered[index+1].id); } setMenu(null); }}>Move later</MenuItem>
      <MenuItem onClick={() => { setDeleting(menu?.link || null); setMenu(null); }} sx={{ color: 'error.main' }}>Delete</MenuItem>
    </Menu>
    <Popover open={!!anchor && !config.PRESENTATION_MODE} anchorEl={anchor} onClose={() => setAnchor(null)} anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}>
      <Stack spacing={1.5} sx={{ p: 2, width: { xs: 300, sm: 360 }, maxWidth: 'calc(100vw - 32px)' }} component="form" onSubmit={event => { event.preventDefault(); save(); }}>
        <Typography variant="subtitle1">{editing ? 'Edit social link' : 'Add social link'}</Typography>
        <TextField size="small" label="Profile URL" value={url} autoFocus required onChange={event => setUrl(event.target.value)} helperText="Paste a URL, @handle (Instagram), or @user@instance." />
        <TextField size="small" label="Label (optional)" value={label} onChange={event => setLabel(event.target.value)} inputProps={{ maxLength: 200 }} />
        <Box aria-live="polite" sx={{ minHeight: 28 }}>
          {previewing ? <Stack direction="row" spacing={1} alignItems="center"><CircularProgress size={14} /><Typography variant="caption">Checking URL…</Typography></Stack> : preview && <Stack direction="row" spacing={1} alignItems="center"><SocialBrandIcon link={preview} /><Typography variant="body2">{preview.brand?.display_name || preview.platform} · {preview.handle}</Typography></Stack>}
        </Box>
        {error && <Typography variant="caption" color="error">{error}</Typography>}
        {editingLink && (editingLink.platform === 'other' || editingLink.brand_domain) && <Stack spacing={0.5}>
          <Typography variant="caption" color="text.secondary">Site icon: {editingLink.brand?.status === 'pending' ? 'checking…' : editingLink.brand?.status === 'ready' ? 'ready' : 'generic icon'}</Typography>
          <Stack direction="row" spacing={1}><Button size="small" disabled={pending} onClick={() => changeIcon()}>Retry icon</Button><Button size="small" component="label" disabled={pending}>Upload icon<input hidden type="file" accept="image/png,image/jpeg,image/webp,image/x-icon,image/svg+xml" onChange={event => { const file=event.target.files?.[0]; if (file) changeIcon(file); event.target.value=''; }} /></Button></Stack>
        </Stack>}
        <Stack direction="row" spacing={1} justifyContent="flex-end"><Button onClick={() => setAnchor(null)}>Cancel</Button><Button variant="contained" type="submit" disabled={!preview || previewing || pending}>Save</Button></Stack>
      </Stack>
    </Popover>
    <Snackbar open={!!feedback} autoHideDuration={feedback?.uncertain ? null : 8000} onClose={() => setFeedback(null)}><Alert severity={feedback?.uncertain ? 'warning' : 'error'} onClose={() => setFeedback(null)}>{feedback?.message}</Alert></Snackbar>
    <ConfirmDialog open={!!deleting} title="Delete social link?" message={`Remove ${deleting?.label || deleting?.handle || 'this link'} from this person?`} confirmLabel="Delete" onConfirm={remove} onClose={() => setDeleting(null)} />
  </Box>;
}
