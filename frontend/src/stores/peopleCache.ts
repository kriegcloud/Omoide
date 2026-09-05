import { useListStore } from "./useListStore";
import type { Person, PersonReadSimple } from "../types";

/**
 * Every people grid caches its list under a key starting with this prefix
 * (people-grid-all, people-grid-female, people-grid-hidden, ...). The grid
 * that performed a mutation patches itself in place so scroll position and
 * loaded pages survive; every other variant is dropped so it refetches.
 */
export const PEOPLE_GRID_PREFIX = "people-grid";

type GridPerson = PersonReadSimple & { appearance_count?: number };

const peopleGridKeys = () =>
  Object.keys(useListStore.getState().lists).filter((key) =>
    key.startsWith(PEOPLE_GRID_PREFIX),
  );

/** Backend order: appearance_count desc, id desc. */
export const sortPeople = <T extends GridPerson>(items: T[]): T[] =>
  [...items].sort(
    (a, b) =>
      (b.appearance_count ?? 0) - (a.appearance_count ?? 0) || b.id - a.id,
  );

/** Drop every cached people grid except the given keys. */
export const clearPeopleGrids = (except: string[] = []) => {
  const { clearList } = useListStore.getState();
  peopleGridKeys()
    .filter((key) => !except.includes(key))
    .forEach((key) => clearList(key));
};

/** Remove people from every cached grid (merged, deleted, hidden away). */
export const removePeopleFromGrids = (ids: number[]) => {
  const { removeItems } = useListStore.getState();
  peopleGridKeys().forEach((key) => removeItems(key, ids));
};

/**
 * Patch a person's fields (name, count, gender, hidden state) into every
 * cached grid and re-sort so the grid matches what the backend would return.
 */
export const patchPersonInGrids = (person: Person | PersonReadSimple) => {
  useListStore.setState((state) => {
    let changed = false;
    const lists = { ...state.lists };
    for (const key of Object.keys(lists)) {
      if (!key.startsWith(PEOPLE_GRID_PREFIX)) continue;
      const list = lists[key];
      const index = list.items.findIndex(
        (item: GridPerson) => item.id === person.id,
      );
      if (index === -1) continue;
      const items = [...list.items];
      items[index] = { ...items[index], ...person };
      lists[key] = { ...list, items: sortPeople(items) };
      changed = true;
    }
    return changed ? { lists } : state;
  });
};

/** Re-sort a single grid after local count changes. */
export const resortPeopleGrid = (listKey: string) => {
  useListStore.setState((state) => {
    const list = state.lists[listKey];
    if (!list) return state;
    return {
      lists: { ...state.lists, [listKey]: { ...list, items: sortPeople(list.items) } },
    };
  });
};
