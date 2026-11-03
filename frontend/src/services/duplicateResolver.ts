/**
 * Optimistic duplicate-group resolution.
 *
 * Intent is applied immediately (the group leaves the list and focus moves
 * on); the request runs in the background. A failure is reported and the list
 * is re-read from the server rather than restored from the snapshot, because
 * a destructive resolve can partially commit (some members deleted) before it
 * fails — the server is the only source of truth for what survived.
 */
import type { MutationEvent } from "../stores/mutationBus";

/**
 * Resolves and media deletions are already applied to the loaded groups (the group
 * leaves, deleted members are pruned). Re-reading on them only cost a round trip
 * and, before depth-preserving refreshes, reset the list to its first page.
 */
export function duplicateListNeedsRefresh(event: MutationEvent): boolean {
  return event.type !== "duplicates:resolved" && event.type !== "media:deleted";
}

export type DuplicateResolveAction =
  | "DELETE_FILES"
  | "DELETE_RECORDS"
  | "BLACKLIST_RECORDS"
  | "MARK_NOT_DUPLICATE";

export interface DuplicateResolveSnapshot {
  action: DuplicateResolveAction;
  groupId: number;
  masterId: number | undefined;
  memberIds: number[];
}

export interface DuplicateResolverDeps {
  resolve: (
    groupId: number,
    action: DuplicateResolveAction,
    masterId: number | undefined,
    memberIds: number[],
  ) => Promise<unknown>;
  removeGroup: (snapshot: DuplicateResolveSnapshot) => void;
  onSettled: (snapshot: DuplicateResolveSnapshot) => void;
  onError: (snapshot: DuplicateResolveSnapshot, error: Error) => void;
  refresh: () => void;
  onPendingChange?: (pending: number) => void;
}

export interface DuplicateResolver {
  request: (snapshot: DuplicateResolveSnapshot) => void;
  pendingCount: () => number;
}

export function createDuplicateResolver(deps: DuplicateResolverDeps): DuplicateResolver {
  const inFlight = new Set<number>();
  const changed = () => deps.onPendingChange?.(inFlight.size);

  const request = (snapshot: DuplicateResolveSnapshot) => {
    if (inFlight.has(snapshot.groupId)) return;
    inFlight.add(snapshot.groupId);
    deps.removeGroup(snapshot);
    changed();
    const master = snapshot.action === "MARK_NOT_DUPLICATE" ? undefined : snapshot.masterId;
    void deps
      .resolve(snapshot.groupId, snapshot.action, master, snapshot.memberIds)
      .then(
        () => deps.onSettled(snapshot),
        (error: unknown) => {
          deps.onError(snapshot, error instanceof Error ? error : new Error(String(error)));
          deps.refresh();
        },
      )
      .finally(() => {
        inFlight.delete(snapshot.groupId);
        changed();
      });
  };

  return { request, pendingCount: () => inFlight.size };
}

interface PrunableGroup {
  group_id: number;
  items: { id: number }[];
}

/**
 * Drop deleted media from loaded groups (e.g. "Delete files…" in the
 * selection bar). A group with fewer than two members is no longer a
 * duplicate group and leaves the list. Returns the same array when nothing
 * changed so React and the list store can skip work.
 */
export function pruneDuplicateGroups<G extends PrunableGroup>(
  groups: G[],
  deletedIds: number[],
): { groups: G[]; removedGroupIds: number[]; changed: boolean } {
  const deleted = new Set(deletedIds);
  const removedGroupIds: number[] = [];
  let changed = false;
  const next: G[] = [];
  for (const group of groups) {
    const items = group.items.filter((item) => !deleted.has(item.id));
    if (items.length === group.items.length) {
      next.push(group);
      continue;
    }
    changed = true;
    if (items.length < 2) removedGroupIds.push(group.group_id);
    else next.push({ ...group, items });
  }
  return changed ? { groups: next, removedGroupIds, changed } : { groups, removedGroupIds, changed };
}
