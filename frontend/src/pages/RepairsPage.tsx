import { useCallback, useEffect, useRef, useState } from "react";
import { useVisiblePolling } from "../hooks/useVisiblePolling";
import { Alert, Button, Chip, CircularProgress, Container, Link, Paper, Stack, Typography } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import type { ImageRepairJob } from "../types";
import { cancelRepair, listRepairs } from "../services/repairs";

const labels: Record<string, string> = {
  "omoide-remove-text-v1": "Remove overlays",
  "omoide-upscale-v1": "Upscale",
  "omoide-remove-people-v1": "Remove other people",
  "omoide-background-swap-v1": "Swap background",
};

export default function RepairsPage() {
  const [jobs, setJobs] = useState<ImageRepairJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const pagesLoaded = useRef(1);
  const pending = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const load = useCallback(async (isCurrent: () => boolean = () => mounted.current) => {
    if (pending.current) return;
    pending.current = true;
    setLoading(true);
    try {
      const rows: ImageRepairJob[] = [];
      let cursor: string | null = null;
      for (let pageIndex = 0; pageIndex < pagesLoaded.current; pageIndex += 1) {
        const page = await listRepairs({ cursor });
        if (!isCurrent()) return;
        rows.push(...page.items);
        cursor = page.next_cursor;
        if (!cursor) break;
      }
      setJobs(Array.from(new Map(rows.map((job) => [job.id, job])).values()));
      setNextCursor(cursor);
      setError(null);
    } catch (reason) {
      if (isCurrent()) setError(reason instanceof Error ? reason.message : "Failed to load repairs");
    } finally {
      pending.current = false;
      if (isCurrent()) setLoading(false);
    }
  }, []);
  useVisiblePolling(load, 3000, true, true);
  const loadMore = async () => {
    if (!nextCursor || pending.current) return;
    pending.current = true;
    setLoading(true);
    try {
      const page = await listRepairs({ cursor: nextCursor });
      if (!mounted.current) return;
      setJobs((rows) => Array.from(new Map([...rows, ...page.items].map((job) => [job.id, job])).values()));
      setNextCursor(page.next_cursor);
      pagesLoaded.current += 1;
      setError(null);
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : "Failed to load more repairs");
    } finally {
      pending.current = false;
      if (mounted.current) setLoading(false);
    }
  };
  const cancel = async (jobId: string) => {
    try { await cancelRepair(jobId); await load(); }
    catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : "Failed to cancel repair"); }
  };
  return (
    <Container maxWidth="md" sx={{ py: 4 }}>
      <Typography variant="h4" gutterBottom>Repairs</Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }} action={<Button disabled={loading} onClick={() => void load()}>Retry</Button>}>{error}</Alert>}
      <Stack spacing={1.5}>
        {jobs.map((job) => (
          <Paper key={job.id} variant="outlined" sx={{ p: 2 }}>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
              <Typography flex={1}>{labels[job.profile] ?? job.profile}</Typography>
              <Chip size="small" label={job.status} color={job.status === "succeeded" ? "success" : job.status === "failed" ? "error" : "default"} />
              <Link component={RouterLink} to={`/medium/${job.result_media_id ?? job.media_id}`}>View media</Link>
              {["created", "queued", "running"].includes(job.status) && <Button size="small" onClick={() => void cancel(job.id)}>Cancel</Button>}
            </Stack>
            {job.error_message && <Typography variant="body2" color="error" mt={1}>{job.error_message}</Typography>}
          </Paper>
        ))}
        {loading && <CircularProgress size={24} />}
        {nextCursor && <Button disabled={loading} onClick={() => void loadMore()}>Load more repairs</Button>}
        {!loading && !jobs.length && !error && <Typography color="text.secondary">No repair jobs yet.</Typography>}
      </Stack>
    </Container>
  );
}
