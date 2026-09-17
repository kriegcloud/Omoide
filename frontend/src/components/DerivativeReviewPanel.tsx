import { useEffect, useState } from "react";
import { Alert, Box, CircularProgress, Paper, Stack, Typography } from "@mui/material";
import type { CurationClient } from "../services/curation";
import type { CurationItem, CurationSource } from "../types/curation";

interface Props {
  client: CurationClient;
  item: CurationItem;
  source: CurationSource;
  onReady: (ready: boolean) => void;
  fixture?: boolean;
}

/** Only authenticated, hash-verified original bytes are used for this comparison. */
export default function DerivativeReviewPanel({ client, item, source, onReady, fixture = true }: Props) {
  const [images, setImages] = useState<{ before: string; after: string } | null>(null);
  const [loaded, setLoaded] = useState({ before: false, after: false });
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const urls: string[] = [];
    setImages(null);
    setLoaded({ before: false, after: false });
    setError(null);
    onReady(false);
    void Promise.all([
      client.image("sources", source.id, source.sha256, controller.signal),
      client.image("artifacts", item.artifact_id, item.sha256, controller.signal),
    ]).then(([before, after]) => {
      if (!active) return;
      urls.push(URL.createObjectURL(before), URL.createObjectURL(after));
      setImages({ before: urls[0], after: urls[1] });
    }).catch(reason => {
      if (active) setError(reason instanceof Error ? reason.message : "The comparison could not be loaded.");
    });
    return () => {
      active = false;
      controller.abort();
      urls.forEach(url => URL.revokeObjectURL(url));
    };
  }, [client, source.id, source.sha256, item.artifact_id, item.sha256, onReady]);

  useEffect(() => { onReady(loaded.before && loaded.after && !error); }, [loaded, error, onReady]);

  return (
    <Stack spacing={2}>
      {error && <Alert severity="error">{error}</Alert>}
      {!images && !error && <Box role="status" sx={{ display: "flex", gap: 1.5, alignItems: "center", minHeight: 240, justifyContent: "center" }}><CircularProgress size={24} /><Typography>Loading comparison…</Typography></Box>}
      {images && (
        <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" }, gap: 2 }}>
          {(["before", "after"] as const).map(side => (
            <Paper key={side} variant="outlined" component="figure" sx={{ m: 0, p: 1.5, minWidth: 0 }}>
              <Typography component="figcaption" variant="subtitle2" sx={{ mb: 1 }}>{side === "before" ? fixture ? "Original fixture" : "Original image" : "Prepared still"}</Typography>
              <Box component="img" src={images[side]} alt={`${side === "before" ? fixture ? "Original fixture" : "Original image" : "Prepared still"}: ${source.label}`}
                onLoad={() => setLoaded(current => ({ ...current, [side]: true }))}
                onError={() => { setError("An image could not be displayed. Refresh before reviewing."); setLoaded({ before: false, after: false }); }}
                sx={{ width: "100%", height: { xs: 280, lg: 400 }, objectFit: "contain", bgcolor: "action.hover", display: "block" }} />
              <Box sx={{ mt: 1 }}><a href={images[side]} target="_blank" rel="noreferrer">Open {side === "before" ? "original" : "prepared still"} at full size</a></Box>
            </Paper>
          ))}
        </Box>
      )}
      <Box>
        <Typography variant="subtitle2">Origin and uncertainty</Typography>
        <Typography variant="body2" color="text.secondary">{source.label} · {item.width} × {item.height} prepared pixels</Typography>
        <Typography variant="body2" sx={{ mt: 0.5 }}>{item.transform_summary ?? "Prepared from the registered source. Generative transformations are unavailable in this review."}</Typography>
        <Typography variant="body2" sx={{ mt: 0.5 }}>{item.uncertainties?.length ? item.uncertainties.join(" ") : `${fixture ? "Fixture evidence only. " : ""}Identity, caption accuracy, and training suitability still require review.`}</Typography>
      </Box>
      <Box component="details" sx={{ overflowWrap: "anywhere" }}>
        <Box component="summary" sx={{ cursor: "pointer", color: "text.secondary" }}>Exact image and caption details</Box>
        <Stack spacing={0.5} sx={{ mt: 1 }}>
          <Typography variant="caption">Original SHA-256: {source.sha256}</Typography>
          <Typography variant="caption">Prepared still reference: {item.artifact_id}</Typography>
          <Typography variant="caption">Prepared still SHA-256: {item.sha256}</Typography>
          {item.caption && <Typography variant="caption">Caption version: {item.caption.id}</Typography>}
          {item.caption && <Typography variant="caption">Caption SHA-256: {item.caption.sha256}</Typography>}
        </Stack>
      </Box>
    </Stack>
  );
}
