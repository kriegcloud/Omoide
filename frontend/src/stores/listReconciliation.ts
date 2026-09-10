import type { MediaPatch } from "./mutationBus";

type Row = { id?: number; group_id?: number; scene_id?: number; type?: string; data?: { id?: number } };
export function listItemId(item: unknown): number | undefined {
  if (!item || typeof item !== "object") return undefined;
  const row = item as Row;
  return row.data?.id ?? row.group_id ?? row.scene_id ?? row.id;
}
function patchItem<T>(item: T, patch: MediaPatch): T {
  const row = item as Row;
  return row.data ? { ...item, data: { ...row.data, ...patch } } : { ...item, ...patch };
}
/** Revisions protect responses started before a mutation, including a full refresh. */
export class ListRevision {
  revision = 0;
  private removed = new Map<number, number>();
  private updated = new Map<number, { revision: number; item: MediaPatch }>();
  remove(ids: Iterable<number>) {
    const revision = ++this.revision;
    for (const id of ids) if (!this.removed.has(id)) this.removed.set(id, revision);
  }
  patch(items: MediaPatch[]) {
    const revision = ++this.revision;
    for (const item of items) this.updated.set(item.id, { revision, item: { ...this.updated.get(item.id)?.item, ...item } });
  }
  reconcile<T>(items: T[], started: number, full: boolean): T[] {
    const result = items.filter(item => {
      const id = listItemId(item);
      const revision = id === undefined ? undefined : this.removed.get(id);
      return revision === undefined || (full && revision <= started);
    }).map(item => {
      const id = listItemId(item);
      const patch = id === undefined ? undefined : this.updated.get(id);
      return patch && (!full || patch.revision > started) ? patchItem(item, patch.item) : item;
    });
    if (full) {
      for (const [id, revision] of this.removed) if (revision <= started) this.removed.delete(id);
      for (const [id, patch] of this.updated) if (patch.revision <= started) this.updated.delete(id);
    }
    return result;
  }
}
/** Different entity kinds may share numeric ids; rows without ids stay distinct. */
export function mergeUnique<T>(previous: T[], incoming: T[]): T[] {
  const result: T[] = [];
  const indices = new Map<string, number>();
  for (const item of [...previous, ...incoming]) {
    const id = listItemId(item);
    const key = id === undefined ? undefined : `${(item as Row).type ?? "item"}:${id}`;
    const index = key === undefined ? undefined : indices.get(key);
    if (index !== undefined) result[index] = item;
    else { if (key !== undefined) indices.set(key, result.length); result.push(item); }
  }
  return result;
}
