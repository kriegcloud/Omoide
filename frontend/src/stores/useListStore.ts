import { useEffect, useMemo, useSyncExternalStore } from "react";
import { mutationBus, type MediaPatch, type MutationEvent } from "./mutationBus";
import { ListRevision, listItemId, mergeUnique } from "./listReconciliation";
import { acknowledgeQuery, isQueryPending, operationVersion, optimisticOperations, projectList, projectEntity, registerQueryEntities, registerQueryRefresh, unregisterQuery } from "./optimisticOperations";
import { entityKey, queryEntityKind, type EntityKind, type EntityKey } from "./queryKeys";
import { pruneDuplicateGroups } from "../services/duplicateResolver";
import { create } from "zustand";
export type PageResponse<T> = import("../types").CursorPage<T> & {total?: number};
import { CursorPage } from "../types";

// Keep the initial fetcher so a mounted card can refresh its owning cached list.
const initialFetchers = new Map<string, () => Promise<CursorPage<unknown>>>();
const generations = new Map<string, number>();
const invalidations = new Map<string, number>();
const revisions = new Map<string, ListRevision>();
const journal = (key: string) => {
  let value = revisions.get(key);
  if (!value) { value = new ListRevision(); revisions.set(key, value); }
  return value;
};
const sceneList = (key: string) => /\/search\/scene(?:\?|$)/.test(key);
const mediaList = (key: string) => !/^(people|orphan|face|duplicate|tags(?:-|$))/.test(key) && !/\/(?:tags?|persons?)(?:\?|$)/.test(key) && !/\/search\/(?:tag|scene)(?:\?|$)/.test(key) && !key.includes("/faces") && !key.endsWith("-timeline");
function membership<T>(key: string, items: T[]): T[] {
  return key.startsWith("favorites") ? items.filter(item => (item as { is_favorite?: boolean }).is_favorite !== false) : items;
}


const cachedRefreshes = new Map<string, Promise<void>>();
export function refreshCachedList(listKey: string): Promise<void> {
  const pending = cachedRefreshes.get(listKey);
  if (pending) return pending;
  const request = (async () => {
    do {
      await performCachedRefresh(listKey);
      // A later committed event may arrive while this GET is in flight. All
      // coalesced callers wait for the subsequent authoritative snapshot too.
    } while (initialFetchers.has(listKey) && useListStore.getState().lists[listKey]?.stale);
  })().finally(() => cachedRefreshes.delete(listKey));
  cachedRefreshes.set(listKey, request);
  return request;
}
async function performCachedRefresh(listKey: string) {
  const fetcher = initialFetchers.get(listKey);
  if (!fetcher) return; // Some cards belong to a page-owned cursor list.
  generations.set(listKey, (generations.get(listKey) ?? 0) + 1);
  useListStore.setState(state => {
    const list = state.lists[listKey];
    return list ? { lists: { ...state.lists, [listKey]: { ...list, stale: true, isLoading: false } } } : state;
  });
  await useListStore.getState().fetchInitial(listKey, fetcher);
  const error = useListStore.getState().lists[listKey]?.error;
  if (error) throw new Error(`List refresh failed: ${error}`);
}

// A generic state shape for any paginated list
export interface ListState<T> {
  items: T[];
  nextCursor: string | null;
  hasMore: boolean;
  isLoading: boolean;
  error: string | null;
  total?: number;
  stale?: boolean;
  loaded?: boolean;
}

// The state for the entire store, holding multiple lists
interface ListStoreState {
  // lists is a dictionary where the key is a unique string (e.g., an API endpoint)
  // and the value is the state of that list.
  lists: Record<string, ListState<unknown>>;

  // Generic actions that work with any data type T
  fetchInitial: <T>(
    listKey: string,
    fetcher: () => Promise<CursorPage<T>>
  ) => Promise<void>;
  loadMore: <T>(
    listKey: string,
    fetcher: (cursor?: string) => Promise<CursorPage<T>>
  ) => Promise<void>;
  removeItem: (listKey: string, itemId: number | string) => void;
  removeItems: (listKey: string, itemIds: (number | string)[]) => void;
  clearList: (listKey: string) => void;
  clearListsByPrefix: (prefix: string) => void;
  addItem: <T>(listKey: string, item: T, position?: "start" | "end") => void;
  updateItem: <T extends { id: unknown }>(listKey: string, item: T) => void;
}

/** The owning page uses one row schema for a cache key, matching its typed
 * fetchInitial/loadMore fetchers. Keep heterogeneous storage unknown and put
 * that key/schema assertion at this read boundary rather than across callers. */
export function readCachedList<T>(state: Pick<ListStoreState, "lists">, listKey: string): ListState<T> | undefined {
  return state.lists[listKey] as ListState<T> | undefined;
}

// The default state for any new list
export const defaultListState: ListState<never> = {
  items: [],
  nextCursor: null,
  hasMore: true,
  isLoading: false,
  error: null,
};

export const useListStore = create<ListStoreState>((set, get) => ({
  lists: {},

  fetchInitial: async <T>(
    listKey: string,
    fetcher: () => Promise<CursorPage<T>>
  ) => {
    initialFetchers.set(listKey, fetcher);
    const generation = generations.get(listKey) ?? 0;
    const revision = journal(listKey).revision;
    const operationsStarted = operationVersion();
    const invalidation = invalidations.get(listKey) ?? 0;
    const existingList = get().lists[listKey];
    // Do not fetch if the list is already loading or if it already has content.
    // A new search will have a new listKey, so this check will allow the fetch.
    if (
      existingList?.isLoading ||
      (existingList && !existingList.stale && existingList.loaded)
    ) {
      return;
    }

    set((state) => ({
      lists: {
        ...state.lists,
        [listKey]: { ...defaultListState, ...existingList, isLoading: true, error: null },
      },
    }));

    try {
      const response = await fetcher();
      if ((generations.get(listKey) ?? 0) !== generation) return;
      const items = membership(listKey, journal(listKey).reconcile(response.items, revision, true));
      acknowledgeQuery(listKey, operationsStarted);
      set((state) => ({
        lists: {
          ...state.lists,
          [listKey]: {
            items,
            loaded: true,
            stale: (invalidations.get(listKey) ?? 0) !== invalidation,
            total: "total" in response ? Math.max(0, Number(response.total) - response.items.length + items.length) : undefined,
            nextCursor: response.next_cursor,
            hasMore: response.next_cursor !== null,
            isLoading: false,
            error: null,
          },
        },
      }));
    } catch (error) {
      if ((generations.get(listKey) ?? 0) !== generation) return;
      console.error(`Failed to fetch initial data for ${listKey}:`, error);
      set((state) => ({
        lists: {
          ...state.lists,
          [listKey]: {
            ...defaultListState,
            ...get().lists[listKey],
            stale: true,
            isLoading: false,
            error: error instanceof Error ? error.message : String(error),
          },
        },
      }));
    }
  },

  loadMore: async <T>(
    listKey: string,
    fetcher: (cursor?: string) => Promise<CursorPage<T>>
  ) => {
    const generation = generations.get(listKey) ?? 0;
    const revision = journal(listKey).revision;
    const currentList = get().lists[listKey] as ListState<T> | undefined;
    if (!currentList || currentList.isLoading || !currentList.hasMore || currentList.error) return;

    set((state) => ({
      lists: { ...state.lists, [listKey]: { ...currentList, isLoading: true } },
    }));

    try {
      const response = await fetcher(currentList.nextCursor ?? undefined);
      if ((generations.get(listKey) ?? 0) !== generation) return;
      // Merge from the latest state, not the pre-await snapshot: items removed
      // (or lists cleared) while the page was in flight must stay removed.
      set((state) => {
        const latest = state.lists[listKey];
        if (!latest) return state;
        return {
          lists: {
            ...state.lists,
            [listKey]: {
              ...latest,
              items: membership(listKey, mergeUnique(latest.items, journal(listKey).reconcile(response.items, revision, false))),
              nextCursor: response.next_cursor,
              hasMore: response.next_cursor !== null,
              isLoading: false,
              error: null,
            },
          },
        };
      });
    } catch (error) {
      if ((generations.get(listKey) ?? 0) !== generation) return;
      console.error(`Failed to load more data for ${listKey}:`, error);
      set((state) => {
        const latest = state.lists[listKey];
        if (!latest) return state;
        return {
          lists: {
            ...state.lists,
            [listKey]: {
              ...latest,
              isLoading: false,
              hasMore: false,
              error: error instanceof Error ? error.message : String(error),
            },
          },
        };
      });
    }
  },
  removeItem: (listKey: string, itemId: number | string) => {
    if (typeof itemId === "number") journal(listKey).remove([itemId]);
    set((state) => {
      const currentList = state.lists[listKey];
      if (!currentList) return state; // If the list doesn't exist, do nothing

      const updatedItems = currentList.items.filter((item: unknown) => {
        const row = item as {group_id?: unknown; id?: unknown};
        const itemIdentifier =
          row.group_id !== undefined ? row.group_id : row.id;

        return itemIdentifier !== itemId;
      });

      return {
        lists: {
          ...state.lists,
          [listKey]: {
            ...currentList,
            items: updatedItems,
            total: currentList.total === undefined ? undefined : Math.max(0, currentList.total - (currentList.items.length - updatedItems.length)),
          },
        },
      };
    });
  },
  removeItems: (listKey, itemIds) => {
    journal(listKey).remove(itemIds.filter((id): id is number => typeof id === "number"));
    set((state) => {
      const currentList = state.lists[listKey];
      if (!currentList) return state;

      // Create a Set of IDs for efficient lookup
      const idsToRemove = new Set(itemIds);
      const updatedItems = currentList.items.filter(
        (item: unknown) => !idsToRemove.has((item as {id: number | string}).id)
      );

      return {
        lists: {
          ...state.lists,
          [listKey]: {
            ...currentList,
            items: updatedItems,
            total: currentList.total === undefined ? undefined : Math.max(0, currentList.total - (currentList.items.length - updatedItems.length)),
          },
        },
      };
    });
  },
  clearList: (listKey: string) => {
    generations.set(listKey, (generations.get(listKey) ?? 0) + 1);
    set((state) => {
      const newLists = { ...state.lists };
      delete newLists[listKey];
      return { lists: newLists };
    });
  },
  // Drops every cached list whose key starts with the prefix, so stale
  // variants (e.g. the other sort order) refetch on their next mount.
  clearListsByPrefix: (prefix: string) => {
    set((state) => {
      const newLists: Record<string, ListState<unknown>> = {};
      for (const [key, value] of Object.entries(state.lists)) {
        if (!key.startsWith(prefix)) newLists[key] = value;
        else generations.set(key, (generations.get(key) ?? 0) + 1);
      }
      return { lists: newLists };
    });
  },
  addItem: (listKey, item, position = "end") => {
    set((state) => {
      const currentList = state.lists[listKey];
      if (!currentList) return state;

      const updatedItems =
        position === "start"
          ? [item, ...currentList.items]
          : [...currentList.items, item];

      return {
        lists: {
          ...state.lists,
          [listKey]: {
            ...currentList,
            items: updatedItems,
            total: currentList.total === undefined ? undefined : Math.max(0, currentList.total - (currentList.items.length - updatedItems.length)),
          },
        },
      };
    });
  },

  updateItem: (listKey, updatedItem) => {
    set((state) => {
      const currentList = state.lists[listKey];
      if (!currentList) return state;
      const updatedItems = currentList.items.map((item: unknown) => {
        if (!item || typeof item !== "object") return item;
        const candidate = item as {
          id?: unknown;
          data?: { id?: unknown };
        };
        if (candidate.data?.id === updatedItem.id) {
          return { ...candidate, data: updatedItem };
        }
        return candidate.id === updatedItem.id ? updatedItem : item;
      });
      return {
        lists: {
          ...state.lists,
          [listKey]: { ...currentList, items: updatedItems },
        },
      };
    });
  },
}));

/** A media mutation must not remove a person/face with the same numeric id. */
function reconcileMediaItem(item: unknown, removed: Set<number>, patches: Map<number, MediaPatch>): unknown | null {
  if (!item || typeof item !== "object") return item;
  const value = item as { id?: number; type?: string; data?: { id?: number }; items?: unknown[] };
  if (value.type && !["media", "image", "video"].includes(value.type)) return item;
  const id = value.data?.id ?? value.id;
  if (id === undefined) return item;
  if (removed.has(id)) return null;
  const patch = patches.get(id);
  return patch ? (value.data ? { ...value, data: { ...value.data, ...patch } } : { ...value, ...patch }) : item;
}
mutationBus.subscribe(event => {
  const state = useListStore.getState();
  if (event.type === "list:invalidate") {
    // Clear unmounted variants; refresh a mounted consumer through its subscription.
    invalidateCachedLists(event.prefix);
    return;
  }
  if (event.type === "person:created" || event.type === "person:changed") {
    invalidateCachedLists("people-grid");
    return;
  }
  if (event.type === "duplicates:resolved") {
    for (const key of Object.keys(state.lists)) if (key.startsWith("duplicate")) state.removeItem(key, event.groupId);
    return;
  }
  if (event.type === "face:deleted" || event.type === "face:assigned") {
    for (const key of Object.keys(state.lists)) if ((key.startsWith("orphan-face") || (event.type === "face:deleted" && key.includes("/faces")))) {
      state.removeItems(key, event.type === "face:deleted" ? event.ids : event.faceIds);
    }
    return;
  }
  if (event.type === "face:detached") { invalidateCachedLists("orphan-faces"); return; }
  if (!["media:deleted", "media:updated", "media:moved"].includes(event.type)) return;
  if (event.type === "media:deleted") {
    for (const key of Object.keys(state.lists)) if (sceneList(key)) invalidateCachedLists(key);
  }
  if (event.type === "media:deleted") {
    useListStore.setState(state => ({ lists: Object.fromEntries(Object.entries(state.lists).map(([key, list]) => {
      if (!key.startsWith("duplicate")) return [key, list];
      const result = pruneDuplicateGroups(list.items as { group_id: number; items: { id: number }[] }[], event.ids);
      return [key, result.changed ? { ...list, items: result.groups, total: list.total === undefined ? undefined : Math.max(0, list.total - result.removedGroupIds.length) } : list];
    })) }));
  }
  const removed = new Set(event.type === "media:deleted" ? event.ids : []);
  const patches = new Map((event.type === "media:updated" || event.type === "media:moved" ? event.items : []).map(item => [item.id, item]));
  const lists = { ...useListStore.getState().lists };
  for (const [key, list] of Object.entries(lists)) {
    if (!mediaList(key)) continue;
    if (removed.size) journal(key).remove(removed);
    if (patches.size) journal(key).patch([...patches.values()]);
    const items = list.items.map((item: unknown) => reconcileMediaItem(item, removed, patches)).filter((item: unknown) => {
      if (item === null) return false;
      return !key.startsWith("favorites") || (item as { is_favorite?: boolean }).is_favorite !== false;
    });
    lists[key] = { ...list, items, total: list.total === undefined ? undefined : Math.max(0, list.total - (list.items.length - items.length)) };
  }
  useListStore.setState({ lists });
  if (event.type === "media:moved") {
    // Folder membership is server-owned. All variants may include a folder predicate.
    for (const key of Object.keys(lists)) if (mediaList(key)) invalidateCachedLists(key);
  }
});

/** Membership, order and derived totals are server-owned. A field patch alone cannot
 * insert newly matching rows, repair cursors, or infer cascaded person deletion.
 * Keep patched rows visible but mark every cached query pending authoritative read. */
mutationBus.subscribe(event => {
  if (event.type !== "list:invalidate") invalidateCachedLists("", false);
});

const always = () => true;
/** Only mounted lists refetch on invalidation; other cached variants stay cleared.
 * `needsRefresh` (a stable function) skips events the view already reconciled. */
export function useListInvalidation(listKey: string, needsRefresh: (event: MutationEvent) => boolean = always) {
  useEffect(() => pinCachedList(listKey), [listKey]);
  useEffect(() => mutationBus.subscribe(event => {
    const shouldRefresh = (event.type !== "list:invalidate" || listKey.startsWith(event.prefix)) && needsRefresh(event);
    if (shouldRefresh) queueMicrotask(() => {
      void refreshCachedList(listKey).catch(error => console.error("Mutation refresh failed", error));
    });
  }), [listKey, needsRefresh]);
}

/** Invalidating preserves rows while an interested mounted view revalidates. */
export function invalidateCachedLists(prefix: string, supersede = true) {
  for (const key of Object.keys(useListStore.getState().lists)) if (key.startsWith(prefix)) {
    invalidations.set(key, (invalidations.get(key) ?? 0) + 1);
    if (supersede) generations.set(key, (generations.get(key) ?? 0) + 1);
  }
  useListStore.setState(state => ({ lists: Object.fromEntries(Object.entries(state.lists).map(([key, list]) =>
    [key, key.startsWith(prefix) ? { ...list, stale: true, ...(supersede ? { isLoading: false } : {}) } : list])) }));
}

/** Explicit adapter; base cache stays authoritative for legacy service callers. */
export function useOptimisticCachedList<T>(listKey: string, kind: EntityKind | undefined = queryEntityKind(listKey)): ListState<T> | undefined {
  const base = useListStore(state => readCachedList<T>(state, listKey));
  const operations = useSyncExternalStore(optimisticOperations.subscribe, optimisticOperations.getSnapshot, optimisticOperations.getSnapshot);
  useEffect(() => pinCachedList(listKey), [listKey]);
  useEffect(() => registerQueryRefresh(listKey, () => refreshCachedList(listKey)), [listKey]);
  useEffect(() => {
    if (!kind || !base) return;
    return registerQueryEntities(listKey, base.items.flatMap(item => {
      const id = listItemId(item); return id === undefined ? [] : [entityKey(kind, id)];
    }));
  }, [listKey, kind, base]);
  return useMemo(() => {
    // operation snapshot is a dependency even when base has not changed.
    void operations;
    if (!base) return undefined;
    return { ...base, ...projectList(listKey, kind, base.items, base.total) };
  }, [base, operations, listKey, kind]);
}
const activeLists = new Map<string, number>();
const lastUsed = new Map<string, number>();
export function pinCachedList(key: string): () => void {
  activeLists.set(key, (activeLists.get(key) ?? 0) + 1);
  lastUsed.set(key, Date.now());
  return () => {
    const count = (activeLists.get(key) ?? 1) - 1;
    if (count) activeLists.set(key, count); else activeLists.delete(key);
  };
}
/** Evict only inactive/unpinned queries and all their associated metadata. */
export function evictCachedLists(limit = 80): string[] {
  const lists = { ...useListStore.getState().lists };
  const candidates = Object.keys(lists).filter(key => !activeLists.has(key) && !isQueryPending(key))
    .sort((a, b) => (lastUsed.get(a) ?? 0) - (lastUsed.get(b) ?? 0));
  const removed: string[] = [];
  while (Object.keys(lists).length > limit && candidates.length) {
    const key = candidates.shift()!;
    if (lists[key].isLoading) continue;
    delete lists[key]; initialFetchers.delete(key); revisions.delete(key); generations.delete(key); lastUsed.delete(key); unregisterQuery(key);
    removed.push(key);
  }
  if (removed.length) useListStore.setState({ lists });
  return removed;
}

/** Detail adapter shares the same overlays and lifetime as every list adapter. */
export function useOptimisticEntity<T>(key: EntityKey, base: T, query = "detail"): T | null {
  const operations = useSyncExternalStore(optimisticOperations.subscribe, optimisticOperations.getSnapshot, optimisticOperations.getSnapshot);
  return useMemo(() => {
    void operations;
    return projectEntity(key, base, query);
  }, [key, base, query, operations]);
}
