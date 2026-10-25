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

/** Detail changes may affect sort order, filter membership and cursor boundaries.
 * Those invariants belong to the server; discard variants instead of applying
 * the default comparator to name/favorite/filtered grids. */
export const patchPersonInGrids = (_person: Person | PersonReadSimple) => {
  void _person;
  clearPeopleGrids();
};

/** A local count change can alter cursor boundaries; reload server order. */
export const resortPeopleGrid = (listKey: string) => {
  useListStore.getState().clearList(listKey);
};
