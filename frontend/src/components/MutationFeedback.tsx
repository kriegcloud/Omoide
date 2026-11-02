import { useSyncExternalStore } from "react";
import { Alert, Box, Button, LinearProgress, Snackbar, Stack } from "@mui/material";
import { optimisticOperations, reconcileOperation, hasOperationLookup, canRefreshOperation, refreshOperation } from "../stores/optimisticOperations";

/** One app-level owner: requests and errors outlive dialogs, routes and list mounts. */
export function MutationFeedback() {
  const records = useSyncExternalStore(optimisticOperations.subscribe, optimisticOperations.getSnapshot, optimisticOperations.getSnapshot);
  const active = records.filter(record => !record.dismissed && record.state !== "reconciled");
  if (!active.length) return null;
  // Keep unresolved/error feedback above success messages without hiding failures.
  const important = active.filter(record => record.error || record.refreshError || ["unknown", "pending", "applied"].includes(record.state));
  const shown = (important.length ? important : active).slice(-3);
  return (
    <Snackbar open anchorOrigin={{ vertical: "bottom", horizontal: "right" }}>
      <Stack spacing={1} sx={{ maxWidth: 440 }} aria-live="polite">
        {shown.map(record => {
          const pending = record.state === "pending" || record.state === "applied";
          const uncertain = record.state === "unknown";
          return (
            <Alert key={record.id} severity={record.state === "rejected" ? "error" : uncertain || record.refreshError || record.state === "partially_committed" ? "warning" : pending ? "info" : "success"}
              onClose={() => optimisticOperations.dismiss(record.id)}
              action={uncertain && (hasOperationLookup(record.id) || canRefreshOperation(record.id)) ? <Button color="inherit" size="small" onClick={() => { void (hasOperationLookup(record.id) ? reconcileOperation(record.id) : refreshOperation(record.id)).catch(() => {}); }}>Check status</Button> : undefined}>
              <Box>{record.label}: {pending ? record.feedback === "job" ? "Submitting…" : "Saving…" : uncertain ? "Outcome uncertain; reconciling" : record.state === "rejected" ? "Not applied" : record.state === "partially_committed" ? "Partly applied" : record.feedback === "job" ? "Accepted; follow progress in tasks" : "Saved"}</Box>
              {record.error && <Box>{record.error}</Box>}
              {record.refreshError && <Box>Refresh/status: {record.refreshError}</Box>}
              {pending && <LinearProgress aria-label={`${record.label} in progress`} sx={{ mt: 1 }} />}
            </Alert>
          );
        })}
      </Stack>
    </Snackbar>
  );
}
