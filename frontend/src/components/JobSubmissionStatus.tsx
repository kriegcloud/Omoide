import { Alert, Stack } from "@mui/material";
import { useJobSubmissions } from "../services/taskOperations";

/** Actual running/terminal state stays in the server-backed task/repair list. */
export default function JobSubmissionStatus({ scope }: { scope: string }) {
  const submissions = useJobSubmissions(scope);
  return <Stack spacing={1} aria-live="polite">
    {submissions.slice(-3).map(row => <Alert key={row.id} severity={row.phase === "failed" ? "error" : ["unknown", "checked"].includes(row.phase) ? "warning" : "info"}>
      {row.label}: {row.phase === "submitting" ? "Submitting…" : row.phase === "accepted" ? "Accepted — see job progress below" : row.phase === "running" ? "Running" : row.phase === "finished" ? `Finished (${row.status})` : row.phase === "checked" ? "Status refreshed; verify the job list before submitting again" : row.phase === "unknown" ? "Outcome uncertain; checking job status. Do not submit again until verified." : "Not accepted"}
    </Alert>)}
  </Stack>;
}
