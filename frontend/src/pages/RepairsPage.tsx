import { useEffect, useState } from "react";
import { Alert, Button, Chip, Container, Link, Paper, Stack, Typography } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import type { ImageRepairJob } from "../types";
import { cancelRepair, listRepairs } from "../services/repairs";

const labels: Record<string, string> = {
  "omoide-remove-text-v1": "Remove overlays",
  "omoide-upscale-v1": "Upscale",
  "omoide-remove-people-v1": "Remove other people",
};

export default function RepairsPage() {
  const [jobs, setJobs] = useState<ImageRepairJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  const load = () => listRepairs().then((page) => setJobs(page.items)).catch((reason) => setError(reason instanceof Error ? reason.message : "Failed to load repairs"));
  useEffect(() => {
    void load();
    const timer = window.setInterval(load, 3000);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <Container maxWidth="md" sx={{ py: 4 }}>
      <Typography variant="h4" gutterBottom>Repairs</Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <Stack spacing={1.5}>
        {jobs.map((job) => (
          <Paper key={job.id} variant="outlined" sx={{ p: 2 }}>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
              <Typography flex={1}>{labels[job.profile] ?? job.profile}</Typography>
              <Chip size="small" label={job.status} color={job.status === "succeeded" ? "success" : job.status === "failed" ? "error" : "default"} />
              <Link component={RouterLink} to={`/medium/${job.result_media_id ?? job.media_id}`}>View media</Link>
              {["created", "queued", "running"].includes(job.status) && <Button size="small" onClick={() => void cancelRepair(job.id).then(load)}>Cancel</Button>}
            </Stack>
            {job.error_message && <Typography variant="body2" color="error" mt={1}>{job.error_message}</Typography>}
          </Paper>
        ))}
        {!jobs.length && !error && <Typography color="text.secondary">No repair jobs yet.</Typography>}
      </Stack>
    </Container>
  );
}
