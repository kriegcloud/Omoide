import { useSyncExternalStore } from "react";
import { DefinitiveMutationError, startOperation } from "../stores/optimisticOperations";

type Submission = { id: string; scope: string; label: string; jobIds?: string[]; status?: string; phase: "submitting" | "accepted" | "running" | "finished" | "checked" | "unknown" | "failed" };
let submissions: readonly Submission[] = [];
const listeners = new Set<() => void>();
const publish = () => listeners.forEach(listener => listener());
const update = (id: string, phase: Submission["phase"]) => {
  submissions = submissions.map(row => row.id === id ? { ...row, phase } : row);
  publish();
};

const refreshers = new Map<string, Set<() => Promise<void>>>();
export function registerJobRefresh(scope: string, refresh: () => Promise<void>) {
  const group = refreshers.get(scope) ?? new Set<() => Promise<void>>();
  group.add(refresh); refreshers.set(scope, group);
  return () => { group.delete(refresh); if (!group.size) refreshers.delete(scope); };
}
export function observeSubmittedJobs(scope: string, jobs: readonly { id: string; status: string }[]) {
  let changed = false;
  submissions = submissions.map(row => {
    if (row.scope !== scope || !row.jobIds?.length) return row;
    const observed = jobs.filter(job => row.jobIds!.includes(job.id));
    if (!observed.length) return row;
    const phase = observed.some(job => job.status === "running") ? "running" : observed.every(job => ["completed", "succeeded", "failed", "cancelled", "interrupted"].includes(job.status)) && observed.length === row.jobIds.length ? "finished" : "accepted";
    const status = observed.map(job => job.status).join(", ");
    if (row.phase === phase && row.status === status) return row;
    changed = true; return { ...row, phase, status };
  });
  if (changed) publish();
}

export function useJobSubmissions(scope: string) {
  return useSyncExternalStore(listener => { listeners.add(listener); return () => { listeners.delete(listener); }; }, () => submissions, () => submissions).filter(row => row.scope === scope);
}
/** Opt-in task acceptance adapter. A successful POST means accepted, never completed.
 * Unknown outcomes perform read-only reconciliation and are never replayed. */
export function submitJob<R>(scope: string, label: string, request: () => Promise<R>, refresh: () => Promise<void>): Promise<R> {
  const id = crypto.randomUUID();
  submissions = [...submissions.slice(-49), { id, scope, label, phase: "submitting" }];
  publish();
  let result: R;
  let failure: unknown;
  const revalidate = async () => {
    const consumers = [...(refreshers.get(scope) ?? [])];
    if (consumers.length) await Promise.all(consumers.map(consumer => consumer()));
    else await refresh();
    if (submissions.find(row => row.id === id)?.phase === "unknown") update(id, "checked");
  };
  const handle = startOperation({ id, label, feedback: "job", input: {}, resources: [`config:job:${scope}`], patches: [],
    request: async () => {
      try {
        result = await request();
        const rows = Array.isArray(result) ? result : [result];
        const jobIds = rows.flatMap(row => row && typeof row === "object" && "id" in row && typeof row.id === "string" ? [row.id] : []);
        submissions = submissions.map(row => row.id === id ? { ...row, jobIds } : row);
        update(id, "accepted"); return result;
      }
      catch (error) { failure = error; throw error; }
    },
    onError: error => update(id, error instanceof DefinitiveMutationError ? "failed" : "unknown"),
    refresh: revalidate, refreshUnknown: revalidate, reconcileUnknownAfterRefresh: true,
  });
  return handle.done.then(record => {
    if (record.state === "rejected" || record.state === "unknown") throw failure;
    return result;
  });
}
/** Only pre-write validation/authorization failures are definitive. */
export function jobError(response: Response, message: string): Error {
  return [400, 403, 404, 422].includes(response.status) ? new DefinitiveMutationError(message) : new Error(message);
}
