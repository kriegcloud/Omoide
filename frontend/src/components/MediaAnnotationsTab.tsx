import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import CancelIcon from "@mui/icons-material/Cancel";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import RefreshIcon from "@mui/icons-material/Refresh";
import ReplayIcon from "@mui/icons-material/Replay";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  approveAnnotation,
  cancelAnnotation,
  createAnnotationRevision,
  getAnnotationAttempt,
  getAnnotationHealth,
  getMediaAnnotations,
  retryAnnotation,
  startAnnotation,
} from "../services/annotations";
import {
  AnnotationAttempt,
  AnnotationHealth,
  AnnotationKind,
  AnnotationTagScore,
  MediaAnnotation,
  MediaAnnotationState,
} from "../types";

const ACTIVE_STATUSES = new Set(["created", "running", "unknown", "lost"]);
const TAG_GROUPS = ["rating", "general", "character"] as const;

function statusColor(status: AnnotationAttempt["status"]) {
  if (status === "succeeded") return "success" as const;
  if (status === "running" || status === "created") return "primary" as const;
  if (status === "cancelled") return "default" as const;
  return "error" as const;
}

function annotationDraft(annotation: MediaAnnotation): string {
  if (annotation.kind === "caption") {
    const text = annotation.content.text;
    return typeof text === "string" ? text : "";
  }
  return JSON.stringify(annotation.content, null, 2);
}

function tagsFor(
  annotation: MediaAnnotation,
  group: (typeof TAG_GROUPS)[number]
): AnnotationTagScore[] {
  const value = annotation.content[group];
  if (!Array.isArray(value)) return [];
  return value.filter(
    (item): item is AnnotationTagScore =>
      typeof item === "object" &&
      item !== null &&
      typeof item.name === "string" &&
      typeof item.score === "number"
  );
}

interface MediaAnnotationsTabProps {
  mediaId: number;
  isVideo: boolean;
}

export function MediaAnnotationsTab({
  mediaId,
  isVideo,
}: MediaAnnotationsTabProps) {
  const [state, setState] = useState<MediaAnnotationState | null>(null);
  const [health, setHealth] = useState<AnnotationHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [attemptDetails, setAttemptDetails] = useState<
    Record<string, AnnotationAttempt>
  >({});

  const load = useCallback(async () => {
    try {
      const [nextState, nextHealth] = await Promise.all([
        getMediaAnnotations(mediaId),
        getAnnotationHealth(),
      ]);
      setState(nextState);
      setHealth(nextHealth);
      setError(null);
    } catch (loadError) {
      setError(
        loadError instanceof Error
          ? loadError.message
          : "Failed to load annotations"
      );
    } finally {
      setLoading(false);
    }
  }, [mediaId]);

  useEffect(() => {
    setLoading(true);
    setAttemptDetails({});
    void load();
  }, [load]);

  const activeAttempt = useMemo(
    () => state?.attempts.find((item) => ACTIVE_STATUSES.has(item.status)),
    [state]
  );

  useEffect(() => {
    if (!activeAttempt && !health?.active_attempt_id) return;
    const timer = window.setInterval(() => void load(), 1500);
    return () => window.clearInterval(timer);
  }, [activeAttempt, health?.active_attempt_id, load]);

  const perform = useCallback(
    async (key: string, operation: () => Promise<unknown>, message: string) => {
      setBusyAction(key);
      setError(null);
      setNotice(null);
      try {
        await operation();
        setNotice(message);
        await load();
      } catch (operationError) {
        setError(
          operationError instanceof Error
            ? operationError.message
            : "Annotation operation failed"
        );
      } finally {
        setBusyAction(null);
      }
    },
    [load]
  );

  const generate = (kind: AnnotationKind) =>
    perform(
      `generate-${kind}`,
      () => startAnnotation(mediaId, kind),
      `${kind === "caption" ? "Caption" : "Tag"} generation started.`
    );

  const loadAttemptEvidence = async (attemptId: string) => {
    setBusyAction(`evidence-${attemptId}`);
    setError(null);
    try {
      const detail = await getAnnotationAttempt(attemptId);
      setAttemptDetails((current) => ({ ...current, [attemptId]: detail }));
    } catch (evidenceError) {
      setError(
        evidenceError instanceof Error
          ? evidenceError.message
          : "Failed to load raw annotation evidence"
      );
    } finally {
      setBusyAction(null);
    }
  };

  const saveRevision = (annotation: MediaAnnotation) => {
    const draft = drafts[annotation.id] ?? annotationDraft(annotation);
    let content: Record<string, unknown>;
    if (annotation.kind === "caption") {
      content = { text: draft.trim() };
    } else {
      try {
        const parsed: unknown = JSON.parse(draft);
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
          throw new Error("Tag content must be a JSON object.");
        }
        content = parsed as Record<string, unknown>;
      } catch (parseError) {
        setError(
          parseError instanceof Error ? parseError.message : "Invalid tag JSON"
        );
        return;
      }
    }
    void perform(
      `save-${annotation.id}`,
      () => createAnnotationRevision(annotation.id, content),
      "Saved a new immutable review revision."
    );
  };

  if (loading && state === null) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 5 }}>
        <CircularProgress />
      </Box>
    );
  }

  const captionReady = health?.profiles.includes("omoide-caption-v1") ?? false;
  const tagsReady = health?.profiles.includes("omoide-tags-v1") ?? false;
  const backendReady = Boolean(health?.enabled && health.ready);
  const globalAttemptId = health?.active_attempt_id;
  const globallyBusy = Boolean(
    globalAttemptId && globalAttemptId !== activeAttempt?.id
  );
  const generateDisabled = Boolean(
    isVideo || activeAttempt || globalAttemptId || busyAction
  );

  return (
    <Stack spacing={2.5}>
      <Box>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={1.5}
          alignItems={{ xs: "stretch", sm: "center" }}
          justifyContent="space-between"
        >
          <Box>
            <Typography variant="h6">Dataset annotations</Typography>
            <Typography variant="body2" color="text.secondary">
              Generate candidate evidence through pinned ComfyUI workflows,
              then edit and approve an immutable revision. Accepted media tags
              are never changed automatically.
            </Typography>
          </Box>
          <Button
            startIcon={<RefreshIcon />}
            onClick={() => void load()}
            disabled={Boolean(busyAction)}
          >
            Refresh
          </Button>
        </Stack>
      </Box>

      {error && <Alert severity="error">{error}</Alert>}
      {notice && <Alert severity="success">{notice}</Alert>}
      {globallyBusy && (
        <Alert severity="info">
          The single annotation worker is occupied by attempt {globalAttemptId}.
          Generation here will unlock when that attempt reaches a resolved state.
        </Alert>
      )}
      {isVideo && (
        <Alert severity="info">
          This first production slice accepts still images only. Scene-level
          video annotation remains behind the benchmark gate.
        </Alert>
      )}
      {!backendReady && (
        <Alert severity="warning">
          ComfyUI annotation is unavailable. The rest of Omoide remains fully
          usable. {health?.detail ?? "The host bridge did not report ready."}
        </Alert>
      )}
      {backendReady &&
        health &&
        Object.keys(health.unavailable_profiles).length > 0 && (
          <Alert severity="info">
            Some locked profiles are not staged yet:{" "}
            {Object.entries(health.unavailable_profiles)
              .map(([profile, reason]) => `${profile} (${reason})`)
              .join(", ")}.
          </Alert>
        )}

      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
        <Button
          variant="contained"
          startIcon={<AutoAwesomeIcon />}
          disabled={generateDisabled || !backendReady || !captionReady}
          onClick={() => void generate("caption")}
        >
          Generate caption
        </Button>
        <Button
          variant="outlined"
          startIcon={<AutoAwesomeIcon />}
          disabled={generateDisabled || !backendReady || !tagsReady}
          onClick={() => void generate("tags")}
        >
          Generate scored tags
        </Button>
      </Stack>

      <Divider />

      <Box>
        <Typography variant="subtitle1" gutterBottom>
          Attempts
        </Typography>
        {!state?.attempts.length ? (
          <Typography variant="body2" color="text.secondary">
            No annotation attempts yet.
          </Typography>
        ) : (
          <Stack spacing={1.5}>
            {state.attempts.map((attempt) => (
              <Card key={attempt.id} variant="outlined">
                <CardContent>
                  <Stack
                    direction={{ xs: "column", sm: "row" }}
                    justifyContent="space-between"
                    spacing={1}
                  >
                    <Box>
                      <Stack direction="row" spacing={1} alignItems="center">
                        <Typography variant="subtitle2">
                          {attempt.kind === "caption" ? "Caption" : "Tags"}
                        </Typography>
                        <Chip
                          size="small"
                          label={attempt.status}
                          color={statusColor(attempt.status)}
                        />
                        <Chip size="small" label={attempt.profile_id} />
                      </Stack>
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{ overflowWrap: "anywhere" }}
                      >
                        {attempt.id}
                      </Typography>
                    </Box>
                    <Stack direction="row" spacing={1}>
                      {ACTIVE_STATUSES.has(attempt.status) && (
                        <Button
                          size="small"
                          color="error"
                          startIcon={<CancelIcon />}
                          disabled={Boolean(busyAction)}
                          onClick={() =>
                            void perform(
                              `cancel-${attempt.id}`,
                              () => cancelAnnotation(attempt.id),
                              "Cancellation requested."
                            )
                          }
                        >
                          {attempt.status === "unknown" || attempt.status === "lost"
                            ? "Resolve / cancel"
                            : "Cancel"}
                        </Button>
                      )}
                      {attempt.retryable && !ACTIVE_STATUSES.has(attempt.status) && (
                        <Button
                          size="small"
                          startIcon={<ReplayIcon />}
                          disabled={Boolean(
                            activeAttempt || globalAttemptId || busyAction || !backendReady
                          )}
                          onClick={() =>
                            void perform(
                              `retry-${attempt.id}`,
                              () => retryAnnotation(attempt.id),
                              "Started a linked retry attempt."
                            )
                          }
                        >
                          Retry
                        </Button>
                      )}
                    </Stack>
                  </Stack>
                  {attempt.error_message && (
                    <Alert severity="error" sx={{ mt: 1.5 }}>
                      {attempt.error_code && `${attempt.error_code}: `}
                      {attempt.error_message}
                    </Alert>
                  )}
                  {!attemptDetails[attempt.id] && (
                    <Button
                      size="small"
                      sx={{ mt: 1.5 }}
                      disabled={Boolean(busyAction)}
                      onClick={() => void loadAttemptEvidence(attempt.id)}
                    >
                      Load raw evidence
                    </Button>
                  )}
                  {attemptDetails[attempt.id]?.raw_result && (
                    <Accordion disableGutters elevation={0} sx={{ mt: 1.5 }}>
                      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                        <Typography variant="body2">
                          Raw backend result and provenance
                        </Typography>
                      </AccordionSummary>
                      <AccordionDetails>
                        <Box
                          component="pre"
                          sx={{
                            m: 0,
                            p: 1.5,
                            bgcolor: "action.hover",
                            borderRadius: 1,
                            overflowX: "auto",
                            whiteSpace: "pre-wrap",
                            overflowWrap: "anywhere",
                            fontSize: "0.75rem",
                          }}
                        >
                          {JSON.stringify(
                            {
                              raw_result: attemptDetails[attempt.id].raw_result,
                              normalized_result:
                                attemptDetails[attempt.id].normalized_result,
                              provenance: attemptDetails[attempt.id].provenance,
                            },
                            null,
                            2
                          )}
                        </Box>
                      </AccordionDetails>
                    </Accordion>
                  )}
                </CardContent>
              </Card>
            ))}
          </Stack>
        )}
      </Box>

      <Divider />

      <Box>
        <Typography variant="subtitle1" gutterBottom>
          Review revisions
        </Typography>
        {!state?.annotations.length ? (
          <Typography variant="body2" color="text.secondary">
            Successful generations will appear here as candidate revisions.
          </Typography>
        ) : (
          <Stack spacing={2}>
            {state.annotations.map((annotation) => (
              <Card key={annotation.id} variant="outlined">
                <CardContent>
                  <Stack
                    direction={{ xs: "column", sm: "row" }}
                    spacing={1}
                    justifyContent="space-between"
                  >
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Typography variant="subtitle2">
                        {annotation.kind === "caption" ? "Caption" : "Scored tags"}
                        {` · revision ${annotation.revision}`}
                      </Typography>
                      <Chip size="small" label={annotation.author} />
                      <Chip
                        size="small"
                        label={annotation.review_status}
                        color={
                          annotation.review_status === "approved"
                            ? "success"
                            : "default"
                        }
                      />
                    </Stack>
                    {annotation.review_status !== "approved" && (
                      <Button
                        size="small"
                        startIcon={<CheckCircleIcon />}
                        disabled={Boolean(busyAction)}
                        onClick={() =>
                          void perform(
                            `approve-${annotation.id}`,
                            () => approveAnnotation(annotation.id),
                            "Approved this revision for future export rendering."
                          )
                        }
                      >
                        Approve
                      </Button>
                    )}
                  </Stack>

                  {annotation.kind === "tags" && (
                    <Stack spacing={1.25} sx={{ mt: 2 }}>
                      {TAG_GROUPS.map((group) => {
                        const tags = tagsFor(annotation, group);
                        const visibleTags = tags.slice(0, 40);
                        return (
                          <Box key={group}>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              sx={{ textTransform: "uppercase" }}
                            >
                              {group}
                            </Typography>
                            <Stack
                              direction="row"
                              spacing={0.75}
                              useFlexGap
                              flexWrap="wrap"
                              sx={{ mt: 0.5 }}
                            >
                              {visibleTags.length ? (
                                visibleTags.map((tag) => (
                                  <Chip
                                    key={`${group}-${tag.name}`}
                                    size="small"
                                    label={`${tag.name} ${tag.score.toFixed(3)}`}
                                  />
                                ))
                              ) : (
                                <Typography variant="body2" color="text.secondary">
                                  None
                                </Typography>
                              )}
                            </Stack>
                            {tags.length > visibleTags.length && (
                              <Typography variant="caption" color="text.secondary">
                                Showing 40 of {tags.length} selected observations.
                              </Typography>
                            )}
                          </Box>
                        );
                      })}
                    </Stack>
                  )}

                  <TextField
                    fullWidth
                    multiline
                    minRows={annotation.kind === "caption" ? 4 : 10}
                    label={
                      annotation.kind === "caption"
                        ? "Caption revision"
                        : "Tag revision JSON"
                    }
                    value={drafts[annotation.id] ?? annotationDraft(annotation)}
                    onChange={(event) =>
                      setDrafts((current) => ({
                        ...current,
                        [annotation.id]: event.target.value,
                      }))
                    }
                    sx={{ mt: 2 }}
                  />
                  <Button
                    size="small"
                    variant="outlined"
                    sx={{ mt: 1.25 }}
                    disabled={Boolean(busyAction)}
                    onClick={() => saveRevision(annotation)}
                  >
                    Save as new revision
                  </Button>
                </CardContent>
              </Card>
            ))}
          </Stack>
        )}
      </Box>
    </Stack>
  );
}
