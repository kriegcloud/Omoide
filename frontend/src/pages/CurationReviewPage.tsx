import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert, Box, Button, Chip, CircularProgress, Container, FormControl,
  InputLabel, MenuItem, Paper, Select, Stack, TextField, Typography,
} from "@mui/material";
import { Link, useNavigate, useParams } from "react-router-dom";
import ConfirmDialog from "../components/ConfirmDialog";
import DerivativeReviewPanel from "../components/DerivativeReviewPanel";
import config from "../config";
import { useHotkeys } from "../hotkeys/useHotkey";
import { createCurationClient, curationBlockerLabel, CurationError, getCurationStatus } from "../services/curation";
import type { CurationClient } from "../services/curation";
import { CurationPasskeyError, confirmCurationPasskey, registerCurationPasskey } from "../services/curationPasskeys";
import type {
  CurationDataset, CurationDatasetSummary, CurationDecision, CurationExportReceipt,
  CurationReviewInput, CurationStatus, CurationAuthStatus,
} from "../types/curation";

const message = (reason: unknown) => reason instanceof Error ? reason.message : "The request could not be completed.";

export default function CurationReviewPage() {
  const [status, setStatus] = useState<CurationStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [credential, setCredential] = useState("");
  const [client, setClient] = useState<CurationClient | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setStatusError(null);
    void getCurationStatus(controller.signal).then(value => {
      if (!controller.signal.aborted) setStatus(value);
    }).catch(reason => {
      if (!controller.signal.aborted) setStatusError(message(reason));
    });
    return () => controller.abort();
  }, [retry]);

  return (
    <Container maxWidth="xl" sx={{ py: 4 }}>
      <Stack spacing={3}>
        <Box><Button component={Link} to="/datasets" sx={{ mb: 1 }}>Back to datasets</Button><Typography variant="h4" component="h1" fontWeight={700}>Still review</Typography><Typography color="text.secondary">Compare each prepared image and its caption before including it in an export.</Typography></Box>
        {statusError ? <Alert severity="error" action={<Button color="inherit" onClick={() => setRetry(value => value + 1)}>Retry</Button>}>{statusError}</Alert>
          : !status ? <Box role="status"><CircularProgress size={24} aria-label="Checking still review availability" /></Box>
          : !status.enabled || !["fixture", "production"].includes(status.mode) ? <Alert severity="info">Still review is disabled. The local operator must enable it before you can connect.</Alert>
          : <>
            <Alert severity="info">{status.mode === "fixture" ? "Fixture preview only. A fixture reviewer credential can approve these test stills. Real human presence is not verified, and acceptance of generative changes is unavailable." : "Production still review. Every accept, reject, or defer decision requires your registered passkey. Generative preparation and acceptance are disabled."}</Alert>
            {status.mode === "production" && <Alert severity="info">Legacy review and approval actions are unavailable. Use Still review to record image and caption decisions.</Alert>}
            {!client ? (
              <Paper component="form" variant="outlined" sx={{ p: 3, maxWidth: 560 }} onSubmit={event => {
                event.preventDefault();
                if (credential.trim()) { setClient(createCurationClient(credential.trim())); setCredential(""); }
              }}>
                <Stack spacing={2}>
                  <Typography variant="h6" component="h2">{status.mode === "fixture" ? "Connect to the fixture review" : "Connect to still review"}</Typography>
                  <TextField label={status.mode === "fixture" ? "Fixture access credential" : "Review access credential"} type="password" value={credential} onChange={event => setCredential(event.target.value)} autoComplete="off" fullWidth helperText="Use the access credential supplied by the local operator. It is kept in memory until you disconnect or close this page." />
                  <Button type="submit" variant="contained" disabled={!credential.trim()}>Connect</Button>
                </Stack>
              </Paper>
            ) : <CurationWorkspace client={client} mode={status.mode as "fixture" | "production"} onDisconnect={() => { setClient(null); setCredential(""); }} />}
          </>}
      </Stack>
    </Container>
  );
}

export function CurationWorkspace({ client, onDisconnect, mode = "fixture" }: { client: CurationClient; onDisconnect: () => void; mode?: "fixture" | "production" }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const [datasets, setDatasets] = useState<CurationDatasetSummary[]>([]);
  const [loaded, setLoaded] = useState<{ route: string; data: CurationDataset } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [refreshRequired, setRefreshRequired] = useState(false);
  const [busy, setBusy] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ artifactId: string; text: string } | null>(null);
  const [rationale, setRationale] = useState("");
  const [readyKey, setReadyKey] = useState<string | null>(null);
  const [comparisonRevision, setComparisonRevision] = useState(0);
  const [decision, setDecision] = useState<CurationReviewInput | null>(null);
  const [receipt, setReceipt] = useState<CurationExportReceipt | null>(null);
  const [auth, setAuth] = useState<CurationAuthStatus | null>(null);
  const generation = useRef(0);
  const pending = useRef<AbortController | null>(null);
  const keys = useRef(new Map<string, string>());
  const captionRef = useRef<HTMLInputElement>(null);
  const data = loaded?.route === (id ?? "") ? loaded.data : null;
  const item = data?.items.find(value => value.artifact_id === activeId) ?? data?.items[0] ?? null;
  const source = data?.sources.find(value => value.id === item?.source_id) ?? null;
  const index = data?.items.findIndex(value => value.artifact_id === item?.artifact_id) ?? -1;
  const captionText = draft?.artifactId === item?.artifact_id ? draft?.text ?? "" : item?.caption?.text ?? "";
  const captionDirty = captionText !== (item?.caption?.text ?? "");
  const itemKey = item && source ? `${item.artifact_id}:${item.sha256}:${source.sha256}` : "";
  const imagesReady = !!itemKey && readyKey === itemKey;
  const production = mode === "production";
  const permitted = (operation: string) => !production || !!data?.actor.operations?.includes(operation);
  const canPreview = permitted("preview");
  const canWrite = !!data && !config.PRESENTATION_MODE && !busy && !refreshRequired;
  const reviewer = production ? data?.actor.kind === "human" && auth?.actor_kind === "human" && auth.enrolled : data?.actor.kind === "fixture_human";
  const canReview = canWrite && permitted("review") && reviewer && !!item?.caption && !item.generative_ancestry && !captionDirty && (!production || (canPreview && imagesReady));
  const canAccept = canReview && imagesReady;
  const canExport = canWrite && permitted("export") && !captionDirty && !!data?.items.length && !data.blockers.length && data.items.some(value => value.eligible);
  const reportReady = useCallback((ready: boolean) => setReadyKey(ready ? itemKey : null), [itemKey]);

  useEffect(() => {
    const controller = new AbortController();
    const version = ++generation.current;
    pending.current?.abort();
    pending.current = null;
    setBusy(false); setLoading(true); setLoaded(null); setError(null); setNotice(null);
    setRefreshRequired(false); setActiveId(null); setDraft(null); setRationale(""); setReadyKey(null); setDecision(null); setReceipt(null); setAuth(null);
    void Promise.all([client.list(controller.signal), production ? client.authStatus(controller.signal) : Promise.resolve(null)]).then(async ([list, authentication]) => {
      if (version !== generation.current) return;
      setDatasets(list); setAuth(authentication);
      const selected = id ?? list[0]?.id;
      if (!selected) return;
      const next = await client.get(selected, controller.signal);
      if (version === generation.current) setLoaded({ route: id ?? "", data: next });
    }).catch(reason => {
      if (version === generation.current) setError(message(reason));
    }).finally(() => {
      if (version === generation.current) setLoading(false);
    });
    return () => { generation.current += 1; controller.abort(); pending.current?.abort(); pending.current = null; };
  }, [client, id, production]);

  const operation = async (run: (signal: AbortSignal) => Promise<void>) => {
    if (pending.current) return;
    const token = new AbortController();
    pending.current = token;
    setBusy(true); setError(null); setNotice(null);
    try { await run(token.signal); }
    catch (reason) {
      if (pending.current !== token) return;
      if (reason instanceof CurationPasskeyError) {
        if (reason.cancelled) setNotice(reason.message);
        else setError(reason.message);
        return;
      }
      setError(message(reason));
      // An ambiguous response must never trigger an automatic duplicate write.
      setRefreshRequired(true);
      setDecision(null);
      if (reason instanceof CurationError && reason.code === "revision_conflict") setNotice("Your decision was not applied. Refresh the review; your caption draft will be kept.");
    } finally {
      if (pending.current === token) { pending.current = null; setBusy(false); }
    }
  };

  const mutate = (run: () => Promise<CurationDataset>, success: string, savedCaption = false) => {
    if (!canWrite) return;
    const version = generation.current;
    return operation(async () => {
      const next = await run();
      if (version !== generation.current) return;
      setLoaded({ route: id ?? "", data: next }); setReceipt(null); setNotice(success);
      if (savedCaption) setDraft(null);
    });
  };

  const refresh = () => {
    const version = generation.current;
    return operation(async () => {
      const [list, authentication] = await Promise.all([client.list(), production ? client.authStatus() : Promise.resolve(null)]);
      const selected = data?.id ?? id ?? list[0]?.id;
      const next = selected ? await client.get(selected) : null;
      if (version !== generation.current) return;
      setDatasets(list); setAuth(authentication); setLoaded(next ? { route: id ?? "", data: next } : null);
      setRefreshRequired(false); setDecision(null); setReadyKey(null);
      setComparisonRevision(value => value + 1);
      setNotice("Review refreshed. Any caption draft is preserved; compare it with the current caption before saving.");
    });
  };

  const move = (offset: number) => {
    if (busy || captionDirty || decision || !data) return;
    const next = data.items[index + offset];
    if (next) { setActiveId(next.artifact_id); setDraft(null); setRationale(""); setReadyKey(null); setNotice(null); }
  };

  const requestDecision = (value: CurationDecision) => {
    if (!canReview || !item?.caption || !data || (value === "accept" && !canAccept)) return;
    setDecision({
      artifact_id: item.artifact_id, caption_id: item.caption.id,
      asset_sha256: item.sha256, caption_sha256: item.caption.sha256,
      decision: value, rationale, expected_revision: data.revision,
    });
  };

  const enroll = () => {
    if (!canWrite || !production || !permitted("enroll") || data?.actor.kind !== "human" || auth?.actor_kind !== "human" || auth.enrolled || !auth.enrollment_available) return;
    const version = generation.current;
    return operation(async signal => {
      const challenge = await client.registrationOptions(signal);
      if (signal.aborted || version !== generation.current) return;
      const credential = await registerCurationPasskey(challenge.public_key, signal);
      if (signal.aborted || version !== generation.current) return;
      const result = await client.register({ challenge_id: challenge.challenge_id, credential }, signal);
      if (signal.aborted || version !== generation.current) return;
      if (result.enrolled !== true) throw new Error("Passkey enrollment could not be confirmed. Refresh before continuing.");
      setAuth({ actor_kind: "human", enrolled: true, enrollment_available: false });
      setNotice("Passkey registered. No review decision has been recorded. Compare the image and caption, then choose and confirm a decision.");
    });
  };

  const confirmDecision = () => {
    if (!decision || !data || !canReview || (decision.decision === "accept" && !canAccept)) return;
    const snapshot = decision;
    const datasetId = data.id;
    const version = generation.current;
    return operation(async signal => {
      let input: Parameters<CurationClient["review"]>[1] = snapshot;
      if (production) {
        const challenge = await client.reviewOptions(datasetId, snapshot, signal);
        if (signal.aborted || version !== generation.current) return;
        const credential = await confirmCurationPasskey(challenge.public_key, signal);
        if (signal.aborted || version !== generation.current) return;
        input = { ...snapshot, presence: { challenge_id: challenge.challenge_id, credential } };
      }
      const next = await client.review(datasetId, input, signal);
      if (signal.aborted || version !== generation.current) return;
      setLoaded({ route: id ?? "", data: next }); setReceipt(null); setDecision(null);
      setNotice(snapshot.decision === "accept" ? production ? "Image and caption accepted." : "Image and caption accepted for this fixture." : snapshot.decision === "reject" ? "Still rejected." : "Still deferred for another review.");
    });
  };

  useHotkeys([{ key: "ArrowLeft", description: "Previous still" }, { key: "ArrowRight", description: "Next still" }], event => move(event.key === "ArrowLeft" ? -1 : 1), { scope: "page", enabled: !!data && !busy && !captionDirty });
  useHotkeys([{ key: "a", description: "Accept image and caption…" }], () => requestDecision("accept"), { scope: "page", enabled: canAccept });
  useHotkeys([{ key: "x", description: "Reject still…", destructive: true }], () => requestDecision("reject"), { scope: "page", enabled: canReview, destructive: true });
  useHotkeys([{ key: "d", description: "Defer still…" }], () => requestDecision("defer"), { scope: "page", enabled: canReview });
  useHotkeys([{ key: "e", description: "Edit caption proposal" }], () => captionRef.current?.focus(), { scope: "page", enabled: canWrite && !!item });

  const requestKey = (name: string) => {
    if (!keys.current.has(name)) keys.current.set(name, crypto.randomUUID());
    return keys.current.get(name)!;
  };

  const exportSnapshot = () => {
    if (!canExport || !data) return;
    const version = generation.current;
    return operation(async () => {
      const next = await client.export(data.id, data.revision, requestKey(`export:${data.id}:${data.revision}`));
      if (version !== generation.current) return;
      setReceipt(next);
      const refreshed = await client.get(data.id);
      if (version === generation.current) setLoaded({ route: id ?? "", data: refreshed });
    });
  };

  const latestReceipt = receipt ?? data?.exports.at(-1);

  const updateReceipt = (resume: boolean) => {
    if (!latestReceipt || (resume && (!canWrite || !permitted("export")))) return;
    const version = generation.current;
    return operation(async () => {
      const next = await (resume ? client.resume(latestReceipt.id) : client.receipt(latestReceipt.id));
      if (version === generation.current) setReceipt(next);
    });
  };

  return (
    <Stack spacing={2} aria-busy={busy || loading}>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
        {data && <FormControl size="small" sx={{ minWidth: 240 }}><InputLabel id="curation-dataset-label">{production ? "Review dataset" : "Fixture dataset"}</InputLabel><Select labelId="curation-dataset-label" label={production ? "Review dataset" : "Fixture dataset"} value={data.id} disabled={busy || captionDirty || !!decision} onChange={event => navigate(`/curation/${encodeURIComponent(event.target.value)}`)}>{datasets.map(value => <MenuItem key={value.id} value={value.id}>{value.name}</MenuItem>)}</Select></FormControl>}
        {data && <Chip label={data.actor.kind === "human" ? "Human reviewer" : data.actor.kind === "fixture_human" ? "Fixture reviewer" : "Agent · proposals only"} variant="outlined" />}
        <Box sx={{ flex: 1 }} />
        <Button disabled={busy || loading} onClick={() => void refresh()}>Refresh review</Button>
        <Button disabled={busy} onClick={onDisconnect}>Disconnect</Button>
      </Stack>
      {config.PRESENTATION_MODE && <Alert severity="info">Read-only mode is active. Review changes and exports are disabled.</Alert>}
      {production && data?.actor.kind === "human" && auth?.actor_kind === "human" && <Paper variant="outlined" sx={{ p: 2 }}><Stack spacing={1}>
        <Typography fontWeight={600}>{auth.enrolled ? "Passkey registered" : auth.enrollment_available && permitted("enroll") ? "Register a passkey to review" : "Passkey setup requires the local operator"}</Typography>
        <Typography variant="body2">{auth.enrolled ? "Each decision requires a fresh passkey confirmation after you review the exact image and caption. Registration alone records no review or approval." : auth.enrollment_available && permitted("enroll") ? "Register the passkey you will use to confirm each decision. Enrollment does not accept or approve any image or caption." : "This access credential cannot enroll a passkey. Ask the local operator to arrange a new enrollment or replace a revoked passkey before reviewing."}</Typography>
        {!auth.enrolled && auth.enrollment_available && permitted("enroll") && <Box><Button variant="outlined" disabled={!canWrite} onClick={() => void enroll()}>Register passkey</Button></Box>}
      </Stack></Paper>}
      {production && data?.actor.kind === "fixture_human" && <Alert severity="warning">Fixture credentials cannot authorize production review. Connect with a production review credential supplied by the local operator.</Alert>}
      {error && <Alert severity="error">{error}</Alert>}
      {notice && <Alert severity="info" role="status">{notice}</Alert>}
      {refreshRequired && <Alert severity="warning">Refresh the review before continuing. A pending decision will not be retried automatically.</Alert>}
      {loading ? <Box role="status"><CircularProgress size={24} aria-label="Loading still review" /></Box> : !data ? <Typography>No review dataset is available for this credential.</Typography> : <>
        <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" spacing={1}>
          <Box><Typography component="h2" variant="h5" fontWeight={700}>{data.name}</Typography><Typography role="status" aria-live="polite">{data.remaining_count} remaining to review · {data.items.length} prepared still{data.items.length === 1 ? "" : "s"}</Typography></Box>
          <Stack direction="row" spacing={1} alignItems="center"><Button disabled={busy || captionDirty || index <= 0} onClick={() => move(-1)}>Previous</Button><Typography variant="body2">{item ? index + 1 : 0} / {data.items.length}</Typography><Button disabled={busy || captionDirty || index >= data.items.length - 1} onClick={() => move(1)}>Next</Button></Stack>
        </Stack>
        {data.sources.filter(value => !data.items.some(candidate => candidate.source_id === value.id)).map(value => (
          <Paper key={value.id} variant="outlined" sx={{ p: 2 }}><Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ sm: "center" }}><Box sx={{ flex: 1 }}><Typography fontWeight={600}>{value.label}</Typography><Typography variant="body2" color="text.secondary">Prepare a separate still for comparison. The original image is preserved.</Typography>{!["registered", "materialized", "ready"].includes(value.status) && <Typography color="warning.main" variant="body2">{curationBlockerLabel(value.status)}</Typography>}</Box><Button variant="outlined" disabled={!canWrite || !permitted("materialize") || captionDirty} onClick={() => { if (!permitted("materialize")) return; void mutate(() => client.materialize(data.id, value.id, data.revision, requestKey(`prepare:${data.id}:${value.id}:${data.revision}`)), "Still prepared. Add a caption, then review the comparison."); }}>Prepare still</Button></Stack></Paper>
        ))}
        {item && source && <>
          {canPreview ? <DerivativeReviewPanel key={`${itemKey}:${data.revision}:${comparisonRevision}`} client={client} item={item} source={source} onReady={reportReady} fixture={!production} /> : <Alert severity="info">Image previews are unavailable for this access credential. Metadata access does not permit viewing images.</Alert>}
          {item.generative_ancestry && <Alert severity="warning">This still has generative changes in its history. Review decisions and export are unavailable for generative images.</Alert>}
          <Paper variant="outlined" sx={{ p: 2.5 }}><Stack spacing={2}>
            <Stack direction="row" spacing={1} alignItems="center"><Typography component="h3" variant="h6">Caption and decision</Typography><Chip size="small" label={item.eligible ? "Accepted · export eligible" : item.review?.decision === "reject" ? "Rejected" : item.review?.decision === "defer" ? "Deferred" : "Needs review"} color={item.eligible ? "success" : "default"} /></Stack>
            {production && item.review?.actor_kind === "human" && item.review.human_presence_verified === true && item.review.presence_evidence?.method === "webauthn" && item.review.presence_evidence.user_present === true && item.review.presence_evidence.user_verified === true && <Typography variant="body2" color="success.main">This recorded decision was verified with the reviewer’s passkey.</Typography>}
            <Box><Typography variant="subtitle2">Current caption</Typography><Typography sx={{ whiteSpace: "pre-wrap" }}>{item.caption?.text ?? "No caption has been proposed yet."}</Typography></Box>
            {item.blockers.length > 0 && <Box>{Array.from(new Set(item.blockers.map(curationBlockerLabel))).map(value => <Typography key={value} variant="body2" color="text.secondary">{value}</Typography>)}</Box>}
            <TextField label="Caption proposal" multiline minRows={3} fullWidth inputRef={captionRef} inputProps={{ maxLength: 8192 }} value={captionText} disabled={!canWrite || !permitted("caption")} onChange={event => setDraft({ artifactId: item.artifact_id, text: event.target.value })} helperText="Saving creates a new caption proposal and clears any prior approval. Review the new image and caption pair before accepting it." />
            <Stack direction="row" spacing={1}><Button variant="outlined" disabled={!canWrite || !permitted("caption") || !captionDirty || !captionText.trim()} onClick={() => { if (!permitted("caption")) return; void mutate(() => client.caption(data.id, item.artifact_id, captionText, data.revision), "Caption proposal saved. Any prior approval is cleared; review this version before accepting.", true); }}>Save caption proposal</Button>{captionDirty && <Button disabled={busy} onClick={() => setDraft(null)}>Use current caption</Button>}</Stack>
            {captionDirty && <Alert severity="info">Save or discard your caption changes before making a decision or moving to another still.</Alert>}
            <TextField label="Review note (optional)" value={rationale} inputProps={{ maxLength: 2048 }} onChange={event => setRationale(event.target.value)} disabled={!canWrite} fullWidth />
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1}><Button variant="contained" disabled={!canAccept} onClick={() => requestDecision("accept")}>Accept image and caption</Button><Button color="error" variant="outlined" disabled={!canReview} onClick={() => requestDecision("reject")}>Reject</Button><Button variant="outlined" disabled={!canReview} onClick={() => requestDecision("defer")}>Defer</Button></Stack>
            {data.actor.kind === "agent" && <Typography variant="body2" color="text.secondary">This agent credential can propose changes within its permissions. A human reviewer is required to record review decisions.</Typography>}
            {!imagesReady && !item.generative_ancestry && <Typography variant="body2" color="text.secondary">{production ? "Both comparison images must finish loading before a review decision is available." : "Both comparison images must finish loading before acceptance is available."}</Typography>}
            <Typography variant="caption" color="text.secondary">Keyboard: A accept, X reject, D defer, E edit caption, ← / → navigate. Decisions open a confirmation. Press ? for help.</Typography>
          </Stack></Paper>
        </>}
        <Paper variant="outlined" sx={{ p: 2.5 }}><Stack spacing={1.5}>
          <Typography component="h3" variant="h6">{production ? "Frozen export" : "Frozen fixture export"}</Typography>
          <Typography variant="body2">Export copies the accepted images and exact caption versions. It does not change the reviewed pixels.</Typography>
          {data.blockers.length > 0 ? <Alert severity="warning"><Typography fontWeight={600}>Export blocked</Typography>{Array.from(new Set(data.blockers.map(curationBlockerLabel))).map(value => <Typography variant="body2" key={value}>{value}</Typography>)}</Alert> : <Typography role="status">{canExport ? "All required reviews are complete. Ready to export." : "Complete the current review before exporting."}</Typography>}
          <Box><Button variant="contained" disabled={!canExport} onClick={() => void exportSnapshot()}>Export accepted stills</Button></Box>
          {latestReceipt && <Alert severity={latestReceipt.status === "succeeded" ? "success" : latestReceipt.status === "blocked" ? "warning" : "info"}><Typography fontWeight={600}>{latestReceipt.status === "succeeded" ? production ? "Export verified" : "Fixture export verified" : latestReceipt.status === "blocked" ? "Export blocked" : "Export is in progress"}</Typography><Typography variant="body2">{latestReceipt.item_count} image and caption pair{latestReceipt.item_count === 1 ? "" : "s"} · reviewed version {latestReceipt.snapshot_revision}</Typography>{latestReceipt.manifest_sha256 && <Box component="details" sx={{ overflowWrap: "anywhere", mt: 1 }}><summary>Verification receipt</summary><Typography variant="caption">Manifest SHA-256: {latestReceipt.manifest_sha256}</Typography></Box>}</Alert>}
          {latestReceipt?.error_code && <Typography variant="body2">{curationBlockerLabel(latestReceipt.error_code)}</Typography>}
          {latestReceipt && latestReceipt.status !== "succeeded" && <Stack direction="row" spacing={1}><Button disabled={busy} onClick={() => void updateReceipt(false)}>Check export status</Button>{latestReceipt.status === "blocked" && <Button disabled={!canWrite || !permitted("export") || captionDirty} onClick={() => void updateReceipt(true)}>Retry this export</Button>}</Stack>}
        </Stack></Paper>
      </>}
      <ConfirmDialog open={decision !== null} title={decision?.decision === "accept" ? "Accept this image and caption?" : decision?.decision === "reject" ? "Reject this still?" : "Defer this still?"}
        message={<Stack spacing={1.5}>
          <Typography>{production ? "Continue with your passkey to record this decision for the exact prepared image, caption, and review note shown here. Cancelling the passkey prompt leaves the decision unsubmitted." : decision?.decision === "accept" ? "This records fixture reviewer acceptance of the exact prepared image and current caption shown here. It does not establish real human presence or authorize generative changes." : decision?.decision === "reject" ? "Record this still as rejected from the draft. The original image is preserved." : "Leave this still for another review. It remains ineligible for export."}</Typography>
          {production && <><Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>Caption: {item?.caption?.text}</Typography>{decision?.rationale && <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>Review note: {decision.rationale}</Typography>}</>}
          {notice && <Alert severity="info" role="status">{notice}</Alert>}
          {error && <Alert severity="error">{error}</Alert>}
        </Stack>}
        confirmLabel={production ? "Confirm with passkey" : decision?.decision === "accept" ? "Accept fixture pair" : decision?.decision === "reject" ? "Reject still" : "Defer still"} confirmColor={decision?.decision === "reject" ? "error" : "primary"} loading={busy}
        onClose={() => { if (!busy) setDecision(null); }} onConfirm={confirmDecision} />
    </Stack>
  );
}
