import { mutationBus, type MutationEvent } from "./mutationBus";
import { listItemId } from "./listReconciliation";
import type { EntityKey, EntityKind, QueryKey } from "./queryKeys";

/** Exact wire shape from app/schemas/mutation_operations.py. */
export interface MutationReceipt {
  operation_id: string;
  state: "pending" | "succeeded" | "partial" | "failed" | "unknown";
  applied: string[];
  skipped: { key: string; reason: string }[];
  failed: { key: string; code: string; message: string }[];
  unknown: string[];
}
export type OperationState = "applied" | "pending" | "committed" | "partially_committed" | "rejected" | "unknown" | "reconciled";
type PatchTarget = { key: EntityKey; query?: QueryKey; baseVersion?: number };
export type OperationPatch = PatchTarget & (
  | { kind: "fields"; values: Readonly<Record<string, unknown>> }
  | { kind: "remove" }
  | { kind: "insert"; item: unknown; position?: "start" | "end" }
  | { kind: "derived"; field: string }
);
export interface OptimisticCommand<I, R> {
  id?: string;
  label: string;
  /** Task POST acknowledgement is acceptance, not completion of the work. */
  feedback?: "job";
  input: Readonly<I>;
  resources: readonly EntityKey[];
  patches: readonly OperationPatch[];
  request: (input: Readonly<I>, id: string) => Promise<R>;
  receipt?: (result: R) => unknown;
  /** Replace a derived intent with authoritative fields, within locked resources. */
  responsePatches?: (result: R) => readonly OperationPatch[];
  /** For transport errors carrying a receipt in an error body. */
  errorReceipt?: (error: unknown) => unknown;
  lookup?: (id: string) => Promise<unknown>;
  /** Atomic single-item UI intent rolls back on every error; refresh still verifies uncertain commits. */
  rollbackOnError?: boolean;
  onError?: (error: unknown) => void;
  /** Legacy commands may release retained intent after authoritative consumer refresh. */
  reconcileUnknownAfterRefresh?: boolean;
  refreshUnknown?: () => Promise<void>;
  /** Called once per newly applied member, before overlay publication. */
  commit?: (receipt: MutationReceipt, newlyApplied: readonly string[]) => void;
  events?: (receipt: MutationReceipt, newlyApplied: readonly string[]) => readonly MutationEvent[];
  refresh?: () => Promise<void>;
}
export interface OperationRecord {
  readonly id: string;
  readonly label: string;
  readonly feedback?: "job";
  readonly sequence: number;
  readonly version: number;
  readonly state: OperationState;
  readonly resources: readonly EntityKey[];
  readonly patches: readonly OperationPatch[];
  readonly receipt?: MutationReceipt;
  readonly error?: string;
  readonly refreshError?: string;
  readonly dismissed?: boolean;
}
export interface OperationHandle {
  id: string;
  /** Always resolves; global feedback owns errors even after a view unmounts. */
  done: Promise<OperationRecord>;
}
export class DefinitiveMutationError extends Error {
  readonly definitive = true;
}
/** Status alone (including 409/500) never establishes rollback safety. */
export function isDefinitiveMutationError(error: unknown): boolean {
  return error instanceof DefinitiveMutationError;
}
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
export function parseMutationReceipt(value: unknown, expectedId?: string): MutationReceipt {
  if (!object(value) || typeof value.operation_id !== "string" || !value.operation_id ||
      (expectedId !== undefined && value.operation_id !== expectedId) ||
      !["pending", "succeeded", "partial", "failed", "unknown"].includes(String(value.state))) throw new Error("Invalid mutation receipt identity/state");
  const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every(x => typeof x === "string" && x.length > 0);
  const applied = value.applied ?? [], unknown = value.unknown ?? [];
  const skipped = value.skipped ?? [], failed = value.failed ?? [];
  if (!strings(applied) || !strings(unknown) || !Array.isArray(skipped) || !Array.isArray(failed) ||
      !skipped.every(x => object(x) && typeof x.key === "string" && typeof x.reason === "string") ||
      !failed.every(x => object(x) && typeof x.key === "string" && typeof x.code === "string" && typeof x.message === "string")) throw new Error("Invalid mutation receipt members");
  const keys = [...applied, ...unknown, ...skipped.map(x => x.key), ...failed.map(x => x.key)];
  if (new Set(keys).size !== keys.length) throw new Error("Overlapping mutation receipt members");
  if (value.state === "succeeded" && (unknown.length || skipped.length || failed.length)) throw new Error("Inconsistent success receipt");
  if (value.state === "failed" && (applied.length || unknown.length)) throw new Error("Inconsistent rejection receipt");
  return structuredClone({ operation_id: value.operation_id, state: value.state, applied, skipped, failed, unknown }) as MutationReceipt;
}

let version = 0, sequence = 0;
const records = new Map<string, OperationRecord>();
let snapshot: readonly OperationRecord[] = [];
const listeners = new Set<() => void>();
const gates = new Map<string, { promise: Promise<void>; release: () => void }>();
const lookups = new Map<string, () => Promise<unknown>>();
const callbacks = new Map<string, Pick<OptimisticCommand<unknown, unknown>, "commit" | "events" | "refresh" | "refreshUnknown" | "reconcileUnknownAfterRefresh">>();
const emitted = new Map<string, Set<string>>();
const acknowledgements = new Map<QueryKey, number>();
const queriesByEntity = new Map<EntityKey, Set<QueryKey>>();
const entitiesByQuery = new Map<QueryKey, Set<EntityKey>>();
const MAX_HISTORY = 100;
const message = (error: unknown) => error instanceof Error ? error.message : String(error);
function unresolved(record: OperationRecord) {
  if (record.state === "reconciled") return false;
  return record.state === "applied" || record.state === "pending" || record.state === "unknown" || record.receipt?.state === "pending" || !!record.receipt?.unknown.length;
}
function publish() {
  // Never evict unresolved operations or committed overlays before acknowledgement.
  const removable = [...records.values()].filter(r => !unresolved(r) && (r.state === "rejected" || r.state === "reconciled") && r.dismissed);
  while (records.size > MAX_HISTORY && removable.length) {
    const record = removable.shift()!;
    records.delete(record.id); callbacks.delete(record.id); emitted.delete(record.id); lookups.delete(record.id);
  }
  snapshot = [...records.values()];
  for (const listener of [...listeners]) {
    try { listener(); } catch (error) { console.error("Operation subscriber failed", error); }
  }
}
function update(id: string, change: Partial<OperationRecord>) {
  const record = records.get(id)!;
  const dataChanged = "patches" in change || "receipt" in change || "state" in change;
  records.set(id, { ...record, ...change, version: dataChanged ? ++version : record.version });
  publish();
  return records.get(id)!;
}
export const operationVersion = () => version;
export const optimisticOperations = {
  subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
  getSnapshot: () => snapshot,
  dismiss(id: string) { if (records.has(id)) update(id, { dismissed: true }); },
};
function release(id: string) { gates.get(id)?.release(); gates.delete(id); }
function settle(id: string, receipt: MutationReceipt): OperationRecord {
  const record = records.get(id)!;
  if (JSON.stringify(record.receipt) === JSON.stringify(receipt)) return record;
  const previous = emitted.get(id) ?? new Set<string>();
  // A lookup must never erase already checkpointed successes.
  if ([...previous].some(key => !receipt.applied.includes(key))) throw new Error("Receipt regressed committed members");
  const newlyApplied = receipt.applied.filter(key => !previous.has(key));
  newlyApplied.forEach(key => previous.add(key)); emitted.set(id, previous);
  const state: OperationState = receipt.state === "pending" ? "pending" : receipt.state === "unknown" || receipt.unknown.length ? "unknown" :
    receipt.state === "succeeded" ? "committed" : receipt.state === "partial" ? "partially_committed" : "rejected";
  // Failed/skipped members lose their overlays; uncertain and successful ones stay.
  const restored = new Set([...receipt.failed, ...receipt.skipped].map(member => member.key));
  const patches = record.patches.filter(patch => !restored.has(patch.key));
  let refreshError: string | undefined;
  try {
    if (newlyApplied.length) {
      callbacks.get(id)?.commit?.(receipt, newlyApplied);
      for (const event of callbacks.get(id)?.events?.(receipt, newlyApplied) ?? []) mutationBus.emit(event);
    }
  } catch (error) { refreshError = `Committed change needs reconciliation: ${message(error)}`; }
  const result = update(id, { state, receipt, patches: state === "rejected" ? [] : patches, error: state === "rejected" || state === "partially_committed" ? receipt.failed.map(x => x.message).join("; ") || "Some members were skipped" : undefined, refreshError });
  if (!unresolved(result)) release(id);
  const refresh = callbacks.get(id)?.refresh;
  if (refresh && !unresolved(result)) {
    void Promise.resolve().then(refresh).catch(error => update(id, { refreshError: message(error) }));
  }
  if (state === "unknown") refreshUncertain(id, callbacks.get(id)!);
  return result;
}
function refreshUncertain(id: string, command: Pick<OptimisticCommand<unknown, unknown>, "refresh" | "refreshUnknown" | "reconcileUnknownAfterRefresh">) {
  const refresh = command.refreshUnknown ?? command.refresh;
  if (!refresh) return;
  void Promise.resolve().then(refresh).then(() => {
    if (command.reconcileUnknownAfterRefresh && records.get(id)?.state === "unknown") {
      update(id, { state: "reconciled", patches: [] }); release(id);
    }
  }).catch(error => update(id, { refreshError: message(error) }));
}
let activeTransports = 0;
const transportWaiters: (() => void)[] = [];
async function acquireTransport(): Promise<() => void> {
  if (activeTransports >= 4) await new Promise<void>(resolve => transportWaiters.push(resolve));
  else activeTransports++;
  return () => {
    const next = transportWaiters.shift();
    if (next) next(); else activeTransports--;
  };
}
export function startOperation<I, R>(command: OptimisticCommand<I, R>): OperationHandle {
  const id = command.id ?? crypto.randomUUID();
  if (records.has(id)) throw new Error(`Operation already exists: ${id}`);
  if (records.size >= 1000) throw new Error("Reconcile retained operations before submitting more work");
  // structuredClone rejects unsnapshotable inputs before applying any intent.
  const input = structuredClone(command.input);
  const patches = structuredClone(command.patches);
  const resources = [...new Set([...command.resources, ...patches.map(patch => patch.key)])];
  const dependencies = [...records.values()].filter(record => unresolved(record) && record.resources.some(key => resources.includes(key))).map(record => gates.get(record.id)?.promise).filter((gate): gate is Promise<void> => !!gate);
  let resolveGate!: () => void;
  gates.set(id, { promise: new Promise<void>(resolve => { resolveGate = resolve; }), release: () => resolveGate() });
  callbacks.set(id, { commit: command.commit, events: command.events, refresh: command.refresh, refreshUnknown: command.refreshUnknown, reconcileUnknownAfterRefresh: command.reconcileUnknownAfterRefresh });
  if (command.lookup) lookups.set(id, () => command.lookup!(id));
  records.set(id, { id, label: command.label, feedback: command.feedback, sequence: ++sequence, version: ++version, state: "applied", resources, patches });
  publish();
  const done = (async () => {
    await Promise.all(dependencies);
    const releaseTransport = await acquireTransport();
    update(id, { state: "pending" });
    try {
      const result = await command.request(input, id);
      const receipt = command.receipt ? parseMutationReceipt(command.receipt(result), id) : {
        operation_id: id, state: "succeeded" as const, applied: resources, failed: [], skipped: [], unknown: [],
      };
      if (command.responsePatches) {
        const responsePatches = structuredClone(command.responsePatches(result));
        if (responsePatches.some(patch => !resources.includes(patch.key))) throw new Error("Response patch escaped operation resources");
        update(id, { patches: responsePatches });
      }
      return settle(id, receipt);
    } catch (error) {
      try {
        const raw = command.errorReceipt?.(error);
        if (raw !== undefined) return settle(id, parseMutationReceipt(raw, id));
      } catch { /* Invalid error receipts remain uncertain. */ }
      command.onError?.(error);
      const definitive = isDefinitiveMutationError(error);
      if (!definitive) refreshUncertain(id, command);
      if (definitive || command.rollbackOnError) {
        const result = update(id, { state: "rejected", patches: [], error: message(error) }); release(id); return result;
      }
      const result = update(id, { state: "unknown", error: message(error) });
      // Lookup is read-only. Never replay an uncertain mutation.
      if (command.lookup) void reconcileOperation(id).catch(() => {});
      return result;
    } finally { releaseTransport(); }
  })();
  return { id, done };
}
export const hasOperationLookup = (id: string): boolean => lookups.has(id);
/** Retry only an opted-in authoritative GET after a failed unknown refresh.
 * This releases legacy intent without claiming the original write succeeded. */
export const canRefreshOperation = (id: string): boolean => {
  const command = callbacks.get(id);
  return Boolean(command?.reconcileUnknownAfterRefresh && (command.refreshUnknown ?? command.refresh));
};
export async function refreshOperation(id: string): Promise<OperationRecord> {
  const command = callbacks.get(id);
  if (!records.has(id) || !canRefreshOperation(id)) throw new Error("Authoritative refresh unavailable");
  try {
    await (command!.refreshUnknown ?? command!.refresh)!();
    if (records.get(id)?.state === "unknown") { update(id, { state: "reconciled", patches: [], refreshError: undefined }); release(id); }
    return records.get(id)!;
  } catch (error) { update(id, { refreshError: message(error) }); throw error; }
}

const inFlightLookups = new Map<string, Promise<OperationRecord>>();
export function reconcileOperation(id: string, lookup = lookups.get(id)): Promise<OperationRecord> {
  const existing = inFlightLookups.get(id);
  if (existing) return existing;
  if (!lookup || !records.has(id)) return Promise.reject(new Error("Operation lookup unavailable"));
  const pending = Promise.resolve().then(lookup).then(raw => settle(id, parseMutationReceipt(raw, id))).catch(error => {
    update(id, { refreshError: `Reconciliation failed: ${message(error)}` }); throw error;
  }).finally(() => inFlightLookups.delete(id));
  inFlightLookups.set(id, pending);
  return pending;
}
/** Inverse is built from the authoritative successful subset, never live selection. */
export async function queueOperationUndo<I, R>(id: string, inverse: (receipt: MutationReceipt) => OptimisticCommand<I, R>): Promise<OperationRecord> {
  const gate = gates.get(id);
  if (gate) await gate.promise;
  const record = records.get(id);
  if (!record?.receipt?.applied.length || unresolved(record)) throw new Error("Undo requires acknowledged successful members");
  return startOperation(inverse(structuredClone(record.receipt))).done;
}
function visiblePatches(query: QueryKey, kind?: EntityKind) {
  const acknowledged = acknowledgements.get(query) ?? -1;
  return snapshot.flatMap(record => {
    if (record.state === "rejected" || record.state === "reconciled" || (!unresolved(record) && record.version <= acknowledged)) return [];
    return record.patches.filter(patch => (!patch.query || patch.query === query) && (!kind || patch.key.startsWith(`${kind}:`)));
  });
}
export function projectEntity<T>(key: EntityKey, base: T, query: QueryKey = "detail"): T | null {
  let value: unknown = base;
  for (const patch of visiblePatches(query).filter(patch => patch.key === key)) {
    if (patch.kind === "remove") value = null;
    else if (patch.kind === "insert") value = patch.item;
    else if (patch.kind === "fields" && value !== null) value = { ...value as object, ...patch.values };
  }
  return value as T | null;
}
export function projectList<T>(query: QueryKey, kind: EntityKind | undefined, base: T[], total?: number): { items: T[]; total?: number } {
  if (!kind) return { items: base, ...(total === undefined ? {} : { total }) };
  let items = base;
  const changes = new Map<string, number>();
  for (const patch of visiblePatches(query, kind)) {
    const matches = (item: T) => `${kind}:${listItemId(item)}` === patch.key;
    if (patch.kind === "remove") { items = items.filter(item => !matches(item)); if (base.some(matches)) changes.set(patch.key, -1); }
    else if (patch.kind === "fields") {
      items = items.map(item => {
        if (!matches(item)) return item;
        const row = item as { data?: object };
        return row.data ? { ...item, data: { ...row.data, ...patch.values } } : { ...item, ...patch.values };
      });
    } else if (patch.kind === "insert") {
      if (!items.some(matches)) {
        items = patch.position === "end" ? [...items, patch.item as T] : [patch.item as T, ...items];
        changes.set(patch.key, base.some(matches) ? 0 : 1);
      }
    }
  }
  return { items, ...(total === undefined ? {} : { total: Math.max(0, total + [...changes.values()].reduce((a, b) => a + b, 0)) }) };
}
/** Capture operationVersion BEFORE GET. Call only after an authoritative successful GET. */
export function acknowledgeQuery(query: QueryKey, started: number) {
  acknowledgements.delete(query); acknowledgements.set(query, started);
  // Metadata for inactive query adapters is bounded. Dropping a fence is conservative.
  if (acknowledgements.size > 200) acknowledgements.delete(acknowledgements.keys().next().value!);
}
/** Reverse index for targeted consumer refresh and cache eviction/pinning. */
const queryRegistrations = new Map<QueryKey, Map<symbol, readonly EntityKey[]>>();
function rebuildQueryEntities(query: QueryKey) {
  for (const key of entitiesByQuery.get(query) ?? []) {
    const queries = queriesByEntity.get(key); queries?.delete(query); if (!queries?.size) queriesByEntity.delete(key);
  }
  const entities = new Set([...(queryRegistrations.get(query)?.values() ?? [])].flat());
  if (entities.size) entitiesByQuery.set(query, entities); else entitiesByQuery.delete(query);
  for (const key of entities) {
    const queries = queriesByEntity.get(key) ?? new Set<QueryKey>(); queries.add(query); queriesByEntity.set(key, queries);
  }
}
export function registerQueryEntities(query: QueryKey, keys: readonly EntityKey[]): () => void {
  const token = Symbol(query);
  const registrations = queryRegistrations.get(query) ?? new Map<symbol, readonly EntityKey[]>();
  registrations.set(token, [...keys]); queryRegistrations.set(query, registrations); rebuildQueryEntities(query);
  return () => {
    registrations.delete(token);
    if (!registrations.size) queryRegistrations.delete(query);
    rebuildQueryEntities(query);
  };
}
export function unregisterQuery(query: QueryKey) {
  queryRegistrations.delete(query); rebuildQueryEntities(query); acknowledgements.delete(query);
}
export function affectedQueries(keys: readonly EntityKey[]): QueryKey[] {
  return [...new Set(keys.flatMap(key => [...queriesByEntity.get(key) ?? []]))];
}
export function isQueryPending(query: QueryKey): boolean {
  const keys = entitiesByQuery.get(query);
  return snapshot.some(record => unresolved(record) && record.patches.some(patch => patch.query === query || (!patch.query && (!keys || keys.has(patch.key)))));
}
/** Retire overlays only after the caller has reconciled every affected base/cache. */
export function markOperationReconciled(id: string) {
  const record = records.get(id);
  if (!record || unresolved(record)) throw new Error("Resolve uncertain members before retiring overlays");
  update(id, { state: "reconciled", patches: [] });
}
/** Waits through partial/unknown lookup; suitable for the existing single Undo slot. */
export async function waitForOperation(id: string): Promise<MutationReceipt> {
  const gate = gates.get(id);
  if (gate) await gate.promise;
  const record = records.get(id);
  if (!record?.receipt || unresolved(record) || !record.receipt.applied.length) throw new Error("Operation has no acknowledged inverse");
  return structuredClone(record.receipt);
}
/** Transport adapter: resolve a receipt even when HTTP failed after prefix commits. */
export async function readMutationResponse(response: Response, id: string, lookup: (id: string) => Promise<unknown>): Promise<MutationReceipt> {
  if (response.ok) {
    const body: unknown = await response.json();
    return parseMutationReceipt(object(body) ? body.receipt ?? body : body, id);
  }
  // Error bodies are not receipts on the current backend; operation headers point
  // to the durable lookup. A failed lookup leaves this result unknown.
  return parseMutationReceipt(await lookup(id), id);
}
export function pendingDerived(query: QueryKey, key: EntityKey, field: string): boolean {
  return visiblePatches(query).some(patch => patch.key === key && patch.kind === "derived" && patch.field === field);
}

const refreshRegistrations = new Map<QueryKey, Map<symbol, () => Promise<void>>>();
const pendingRefreshes = new Map<QueryKey, Promise<void>>();
export function registerQueryRefresh(query: QueryKey, refresh: () => Promise<void>): () => void {
  const token = Symbol(query);
  const entries = refreshRegistrations.get(query) ?? new Map<symbol, () => Promise<void>>();
  entries.set(token, refresh); refreshRegistrations.set(query, entries);
  return () => { entries.delete(token); if (!entries.size) refreshRegistrations.delete(query); };
}
/** Coalesce same-query invalidations and share one outcome with every caller. */
export async function revalidateQueries(queries: readonly QueryKey[]): Promise<void> {
  const results = await Promise.allSettled([...new Set(queries)].map(query => {
    const pending = pendingRefreshes.get(query);
    if (pending) return pending;
    const refresh = [...(refreshRegistrations.get(query)?.values() ?? [])].at(-1);
    if (!refresh) return Promise.resolve();
    const request = Promise.resolve().then(refresh).finally(() => pendingRefreshes.delete(query));
    pendingRefreshes.set(query, request); return request;
  }));
  const failed = results.find(result => result.status === "rejected");
  if (failed?.status === "rejected") throw failed.reason;
}

/** Legacy uncertainty can retire only after all targeted consumers perform a GET. */
export async function revalidateUnknownQueries(queries: readonly QueryKey[]): Promise<void> {
  if (!queries.length || queries.some(query => !refreshRegistrations.get(query)?.size)) {
    throw new Error("Authoritative refresh unavailable; reopen the affected list to refresh");
  }
  await revalidateQueries(queries);
}
