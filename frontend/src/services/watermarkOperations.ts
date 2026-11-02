import { API } from '../config';
import { startOperation, parseMutationReceipt, reconcileOperation, DefinitiveMutationError, type MutationReceipt } from '../stores/optimisticOperations';
import { mutationBus } from '../stores/mutationBus';
import { invalidateCachedLists } from '../stores/useListStore';
export interface WatermarkCandidate { id: number; media_id: number; status: string; regions: { text: string; quad?: number[] }[]; preview_path?: string | null; mask_path?: string | null; backup_path?: string | null; error?: string | null }
export async function watermarkJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${url}`, init);
  if (!response.ok) { const message = `Request failed (${response.status}): ${await response.text()}`; if (!url.includes('/backups/purge') && [400, 403, 404, 409, 422].includes(response.status)) throw new DefinitiveMutationError(message); throw new Error(message); }
  return response.status === 204 ? undefined as T : response.json();
}
export const jsonBody = (body: unknown, method = 'POST'): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
export function candidateReceipt(receipt: MutationReceipt, candidates: readonly Pick<WatermarkCandidate, 'id' | 'media_id'>[]): MutationReceipt {
  const key = (value: string) => { const row = candidates.find(c => `media:${c.media_id}` === value); if (!row) throw new Error('Receipt contains an unexpected media member'); return `watermark:${row.id}`; };
  return { ...receipt, applied: receipt.applied.map(key), unknown: receipt.unknown.map(key), failed: receipt.failed.map(x => ({ ...x, key: key(x.key) })), skipped: receipt.skipped.map(x => ({ ...x, key: key(x.key) })) };
}
const pause = () => new Promise<void>(resolve => setTimeout(resolve, 1000));
/** Bounded read-only polling. Unknown outcomes remain in the shared reconciliation UI. */
export async function pollWatermarkReceipt(url: string, id: string, wait = pause): Promise<MutationReceipt> {
  let receipt: MutationReceipt | undefined;
  for (let attempt = 0; attempt < 120; attempt++) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`Operation status failed (${response.status})`);
    const body = await response.json(); receipt = parseMutationReceipt(body.receipt ?? body, id);
    if (receipt.state !== 'pending' && receipt.state !== 'unknown' && !receipt.unknown.length) return receipt;
    await wait();
  }
  throw new Error(`Operation ${receipt?.operation_id ?? id} is still pending; check status to reconcile`);
}
export function changeWatermarks(action: 'approve' | 'reject' | 'restore', candidates: WatermarkCandidate[]) {
  // Backend refuses two candidates for one media in the same operation.
  const rows = [...new Map(candidates.map(c => [c.media_id, c])).values()];
  if (rows.length > 200) { for (let offset = 0; offset < rows.length; offset += 200) changeWatermarks(action, rows.slice(offset, offset + 200)); return; }
  let lookupUrl = '';
  const lookup = async (id: string) => candidateReceipt(await pollWatermarkReceipt(lookupUrl || `${API}/api/watermarks/operations/${id}`, id), rows);
  const handle = startOperation({ label: `${action} ${rows.length} watermark(s)`, input: { ids: rows.map(c => c.id) }, resources: rows.map(c => `watermark:${c.id}` as const), patches: rows.map(c => ({ kind: 'remove' as const, key: `watermark:${c.id}` as const })),
    request: async (input, id) => {
      const response = await fetch(`${API}/api/watermarks/${action}`, jsonBody({ ...input, operation_id: id }));
      const advertised = response.headers.get('X-Mutation-Operation-URL');
      if (advertised) lookupUrl = new URL(advertised, new URL(API || window.location.origin, window.location.origin)).href;
      if (!response.ok) {
        if ([400, 403, 404, 409, 422].includes(response.status) && !advertised) throw new DefinitiveMutationError(await response.text());
        return lookup(id);
      }
      if (action === 'reject') return { operation_id: id, state: 'succeeded', applied: rows.map(c => `watermark:${c.id}`), failed: [], skipped: [], unknown: [] } as MutationReceipt;
      const body = await response.json(); return candidateReceipt(parseMutationReceipt(body.receipt ?? body, id), rows);
    }, receipt: r => r, lookup,
    events: () => [{ type: 'review:changed', scope: 'watermarks' }],
    refresh: async () => { invalidateCachedLists('watermarks'); mutationBus.emit({ type: 'list:invalidate', prefix: 'watermarks' }); },
  });
  void handle.done.then(record => { if (record.receipt?.state === 'pending' || record.state === 'unknown' || record.receipt?.unknown.length) void reconcileOperation(handle.id).catch(() => {}); });
  return handle;
}
/** Auxiliary writes still use the global feedback/lifetime owner. */
export function watermarkWrite(label: string, url: string, init: RequestInit, refresh?: () => Promise<void>) {
  return startOperation({ label, input: {}, resources: [], patches: [], request: async (_input, id) => { const body = typeof init.body === 'string' ? JSON.stringify({ ...JSON.parse(init.body), operation_id: id }) : init.body; return watermarkJson(url, { ...init, body, headers: { ...init.headers, 'X-Mutation-Operation-ID': id } }); }, refresh: async () => { invalidateCachedLists('watermarks'); mutationBus.emit({ type: 'list:invalidate', prefix: 'watermarks' }); await refresh?.(); } });
}
