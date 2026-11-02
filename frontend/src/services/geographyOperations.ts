import { ApiError } from './api';
import { startOperation, DefinitiveMutationError, type OperationPatch } from '../stores/optimisticOperations';
import type { EntityKey } from '../stores/queryKeys';

/** Secondary metadata intents share the global owner and never replay lost writes. */
export function startGeographyMutation<R>(label: string, resource: EntityKey, patches: readonly OperationPatch[], request: () => Promise<R>, refresh: () => Promise<void>, applied?: (result: R) => readonly EntityKey[]) {
  const id = crypto.randomUUID();
  const keys = [...new Set([resource, ...patches.map(patch => patch.key)])];
  return startOperation({ id, label, input: {}, resources: [resource], patches,
    request: async () => {
      try { return await request(); }
      catch (error) {
        // events.py/person/timeline.py and media/scenes.py validate 400/404 before writes.
        // Geolocation 403 can arise during a filesystem update: keep that outcome unknown.
        const statuses = resource.startsWith("media:") ? [401, 404, 422] : [400, 401, 403, 404, 422];
        if (error instanceof ApiError && statuses.includes(error.status)) throw new DefinitiveMutationError(error.message);
        throw error;
      }
    },
    receipt: result => {
      const actual = applied ? applied(result) : keys;
      if (actual.some(key => !keys.includes(key))) throw new Error("Unexpected mutation result member");
      const skipped = keys.filter(key => !actual.includes(key)).map(key => ({key, reason: "not_applied"}));
      return {operation_id: id, state: skipped.length ? actual.length ? "partial" : "failed" : "succeeded", applied: actual, skipped, failed: [], unknown: []};
    },
    refresh, refreshUnknown: refresh, reconcileUnknownAfterRefresh: true,
  });
}
