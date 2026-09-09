import { create } from "zustand";
import { CursorPage } from "../types";

// Keep the initial fetcher so a mounted card can refresh its owning cached list.
const initialFetchers = new Map<string, () => Promise<CursorPage<unknown>>>();
const generations = new Map<string, number>();

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
    const existingList = get().lists[listKey];
    // Do not fetch if the list is already loading or if it already has content.
    // A new search will have a new listKey, so this check will allow the fetch.
    if (
      existingList?.isLoading ||
      (existingList && existingList.items.length > 0)
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
      set((state) => ({
        lists: {
          ...state.lists,
          [listKey]: {
            items: response.items,
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
    const currentList = get().lists[listKey] as ListState<T> | undefined;
    if (!currentList || currentList.isLoading || !currentList.hasMore) return;

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
              items: [...latest.items, ...response.items],
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
