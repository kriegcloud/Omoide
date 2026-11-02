import { useEffect, useSyncExternalStore } from "react";
import { Alert, Box, Button, LinearProgress, Stack } from "@mui/material";
import { optimisticOperations, reconcileOperation, hasOperationLookup, canRefreshOperation, refreshOperation } from "../stores/optimisticOperations";

type FeedbackRecord = ReturnType<typeof optimisticOperations.getSnapshot>[number];
export const SUCCESS_AUTO_CLOSE_MS = 4000;
export const MAX_VISIBLE_NOTICES = 3;

const isPending = (record: FeedbackRecord) => record.state === "pending" || record.state === "applied";
/** Successes close themselves; anything that needs attention stays until closed. */
export function autoCloses(record: FeedbackRecord) {
  return !record.error && !record.refreshError && !isPending(record) &&
    record.state !== "unknown" && record.state !== "rejected" && record.state !== "partially_committed";
}
function message(record: FeedbackRecord) {
  if (isPending(record)) return record.feedback === "job" ? "Submitting…" : "Saving…";
  if (record.state === "unknown") return "Outcome uncertain; reconciling";
  if (record.state === "rejected") return "Not applied";
  if (record.state === "partially_committed") return "Partly applied";
  return record.feedback === "job" ? "Accepted; follow progress in tasks" : "Saved";
}
function severity(record: FeedbackRecord) {
  if (record.state === "rejected") return "error" as const;
  if (record.state === "unknown" || record.refreshError || record.state === "partially_committed") return "warning" as const;
  return isPending(record) ? "info" as const : "success" as const;
}

export interface FeedbackGroup { key: string; records: FeedbackRecord[]; lead: FeedbackRecord }
/**
 * Identical finished notices (same label, outcome and error) collapse into one
 * row with a count; attention-needing notices sort first, newest last.
 */
export function feedbackGroups(records: readonly FeedbackRecord[]): { visible: FeedbackGroup[]; hidden: number } {
  const active = records.filter(record => !record.dismissed && record.state !== "reconciled");
  const groups = new Map<string, FeedbackGroup>();
  for (const record of active) {
    const key = isPending(record) || record.state === "unknown"
      ? record.id // live operations keep their own row (progress, Check status)
      : `${record.label}\u0000${message(record)}\u0000${record.error ?? ""}\u0000${record.refreshError ?? ""}`;
    const group = groups.get(key);
    if (group) { group.records.push(record); group.lead = record; } else groups.set(key, { key, records: [record], lead: record });
  }
  const ordered = [...groups.values()].sort((a, b) => Number(autoCloses(a.lead)) - Number(autoCloses(b.lead)));
  const visible = ordered.slice(0, MAX_VISIBLE_NOTICES);
  return { visible, hidden: ordered.length - visible.length };
}

function AutoClose({ group }: { group: FeedbackGroup }) {
  const close = autoCloses(group.lead);
  const ids = group.records.map(record => record.id).join(",");
  useEffect(() => {
    if (!close) return;
    const timer = window.setTimeout(() => ids.split(",").forEach(id => optimisticOperations.dismiss(id)), SUCCESS_AUTO_CLOSE_MS);
    return () => window.clearTimeout(timer);
  }, [close, ids]);
  return null;
}

/** One app-level owner: requests and errors outlive dialogs, routes and list mounts. */
export function MutationFeedback() {
  const records = useSyncExternalStore(optimisticOperations.subscribe, optimisticOperations.getSnapshot, optimisticOperations.getSnapshot);
  const { visible, hidden } = feedbackGroups(records);
  if (!visible.length) return null;
  const dismissAll = () => records.forEach(record => optimisticOperations.dismiss(record.id));
  return (
    <Stack spacing={1} aria-live="polite"
      sx={{ position: "fixed", right: 16, bottom: 16, zIndex: (theme) => theme.zIndex.snackbar, width: 360, maxWidth: "calc(100vw - 32px)" }}>
      {hidden > 0 && (
        <Button size="small" variant="text" onClick={dismissAll} sx={{ alignSelf: "flex-end" }}>
          +{hidden} more · dismiss all
        </Button>
      )}
      {visible.map(group => {
        const record = group.lead;
        const pending = isPending(record);
        const uncertain = record.state === "unknown";
        const count = group.records.length;
        return (
          <Alert key={group.key} severity={severity(record)}
            sx={{ boxShadow: 3, "& .MuiAlert-message": { flex: 1, minWidth: 0 } }}
            onClose={() => group.records.forEach(item => optimisticOperations.dismiss(item.id))}
            action={uncertain && (hasOperationLookup(record.id) || canRefreshOperation(record.id)) ? <Button color="inherit" size="small" onClick={() => { void (hasOperationLookup(record.id) ? reconcileOperation(record.id) : refreshOperation(record.id)).catch(() => {}); }}>Check status</Button> : undefined}>
            <AutoClose group={group} />
            <Box component="span">{record.label}: {message(record)}{count > 1 ? ` ×${count}` : ""}</Box>
            {record.error && <Box>{record.error}</Box>}
            {record.refreshError && <Box>Refresh/status: {record.refreshError}</Box>}
            {pending && <LinearProgress color="inherit" aria-label={`${record.label} in progress`} sx={{ mt: 1 }} />}
          </Alert>
        );
      })}
    </Stack>
  );
}
