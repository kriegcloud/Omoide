import { useEffect } from "react";
import { mutationBus, type MediaPatch } from "./mutationBus";
import { ListRevision, mergeUnique } from "./listReconciliation";
import { create } from "zustand";
import { CursorPage } from "../types";

// Keep the initial fetcher so a mounted card can refresh its owning cached list.
const initialFetchers = new Map<string, () => Promise<CursorPage<unknown>>>();
const generations = new Map<string, number>();
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


export async function refreshCachedList(listKey: string) {
  const fetcher = initialFetchers.get(listKey);
  if (!fetcher) return; // Some cards belong to a page-owned cursor list.
  useListStore.getState().clearList(listKey);
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
}

// The state for the entire store, holding multiple lists
interface ListStoreState {
  // lists is a dictionary where the key is a unique string (e.g., an API endpoint)
  // and the value is the state of that list.
  lists: Record<string, ListState<any>>;

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

// The default state for any new list
export const defaultListState: ListState<any> = {
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
    const existingList = get().lists[listKey];
    // Do not fetch if the list is already loading or if it already has content.
    // A new search will have a new listKey, so this check will allow the fetch.
    if (
      existingList?.isLoading ||
      (existingList && !existingList.stale && existingList.items.length > 0)
    ) {
      return;
    }

    set((state) => ({
      lists: {
        ...state.lists,
        [listKey]: { ...defaultListState, isLoading: true },
      },
    }));

    try {
      const response = await fetcher();
      if ((generations.get(listKey) ?? 0) !== generation) return;
      const items = membership(listKey, journal(listKey).reconcile(response.items, revision, true));
      set((state) => ({
        lists: {
          ...state.lists,
          [listKey]: {
            items,
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
            isLoading: false,
            hasMore: false,
            error: error instanceof Error ? error.message : String(error),
          },
        },
      }));
    }
  },

  loadMore: async <T>(
    listKey: string,
    fetcher: (cursor: string | null) => Promise<CursorPage<T>>
  ) => {
    const generation = generations.get(listKey) ?? 0;
    const revision = journal(listKey).revision;
    const currentList = get().lists[listKey] as ListState<T> | undefined;
    if (!currentList || currentList.isLoading || !currentList.hasMore || currentList.error) return;

    set((state) => ({
      lists: { ...state.lists, [listKey]: { ...currentList, isLoading: true } },
    }));

    try {
      const response = await fetcher(currentList.nextCursor);
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

      const updatedItems = currentList.items.filter((item: any) => {
        const itemIdentifier =
          item.group_id !== undefined ? item.group_id : item.id;

        return itemIdentifier !== itemId;
      });

      return {
        lists: {
          ...state.lists,
          [listKey]: {
            ...currentList,
            items: updatedItems,
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
        (item: any) => !idsToRemove.has(item.id)
      );

      return {
        lists: {
          ...state.lists,
          [listKey]: {
            ...currentList,
            items: updatedItems,
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
      const newLists: Record<string, ListState<any>> = {};
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
    state.clearListsByPrefix(event.prefix);
    return;
  }
  if (event.type === "person:created" || event.type === "person:changed") {
    state.clearListsByPrefix("people-grid");
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
  if (event.type === "face:detached") { state.clearListsByPrefix("orphan-faces"); return; }
  if (!["media:deleted", "media:updated", "media:moved"].includes(event.type)) return;
  if (event.type === "media:deleted") {
    for (const key of Object.keys(state.lists)) if (sceneList(key)) state.clearList(key);
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
    for (const key of Object.keys(lists)) if (mediaList(key)) state.clearList(key);
  }
});

/** Only mounted lists refetch on invalidation; other cached variants stay cleared. */
export function useListInvalidation(listKey: string) {
  useEffect(() => mutationBus.subscribe(event => {
    const shouldRefresh = (event.type === "list:invalidate" && listKey.startsWith(event.prefix)) ||
      (event.type === "media:moved" && mediaList(listKey)) ||
      (event.type === "media:deleted" && sceneList(listKey)) ||
      ((event.type === "person:created" || event.type === "person:changed") && listKey.startsWith("people-grid")) ||
      (event.type === "face:detached" && listKey.startsWith("orphan-faces"));
    if (shouldRefresh) queueMicrotask(() => {
      const fetcher = initialFetchers.get(listKey);
      if (fetcher) void useListStore.getState().fetchInitial(listKey, fetcher);
    });
  }), [listKey]);
}
