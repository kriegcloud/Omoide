import ConfirmDialog from "../components/ConfirmDialog";
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Container,
  IconButton,
  TextField,
  Typography,
} from "@mui/material";
import TheatersIcon from "@mui/icons-material/Theaters";
import EditIcon from "@mui/icons-material/Edit";
import PhotoAlbumIcon from "@mui/icons-material/PhotoAlbum";
import { useNavigate, useParams } from "react-router-dom";
import config from "../config";
import { CursorMediaGrid } from "../components/CursorMediaGrid";
import { EmptyState } from "../components/EmptyState";
import {
  convertEventToAlbum,
  getEvent,
  getEventMedia,
  updateEvent,
} from "../services/features";
import { EventItem } from "../types";

export default function EventDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const eventId = Number(id);
  const [loadedEvent, setEvent] = useState<EventItem | null>(null);
  const event = loadedEvent?.id === eventId ? loadedEvent : null;
  const routeId = useRef(eventId); routeId.current = eventId;
  const generation = useRef(0);
  const actionBusy = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isRenaming, setIsRenaming] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [isSavingTitle, setIsSavingTitle] = useState(false);
  const [convertTarget, setConvertTarget] = useState<EventItem | null>(null);
  const [isConverting, setIsConverting] = useState(false);

  useEffect(() => {
    const version = ++generation.current;
    const controller = new AbortController();
    const current = () => !controller.signal.aborted && generation.current === version && routeId.current === eventId;
    setEvent(null); setError(null); actionBusy.current = false; setBusy(false);
    setIsRenaming(false); setConvertTarget(null); setIsSavingTitle(false); setIsConverting(false);
    if (Number.isFinite(eventId)) getEvent(eventId, controller.signal)
      .then((data) => { if (current()) { setEvent(data);  } })
      .catch((error) => { if (current()) setError(error instanceof Error ? error.message : "Failed to load event"); });
    return () => { controller.abort(); if (generation.current === version) generation.current += 1; };
  }, [eventId]);

  const fetcher = useCallback(
    (cursor: string | null, signal: AbortSignal) =>
      getEventMedia(eventId, cursor, signal),
    [eventId]
  );

  const dateRange = event
    ? `${new Date(event.start_at).toLocaleDateString()} – ${new Date(
        event.end_at
      ).toLocaleDateString()}`
    : "";

  if (!config.EVENTS_ENABLED) {
    return (
      <Typography variant="h5" color="text.primary" gutterBottom>
        Events disabled!
      </Typography>
    );
  }

  const startRenaming = () => {
    setTitleDraft(event?.title || "");
    setIsRenaming(true);
  };

  const saveTitle = async () => {
    const title = titleDraft.trim();
    if (!title || !event || actionBusy.current) return;
    const target = event.id, version = generation.current;
    actionBusy.current = true; setBusy(true); setIsSavingTitle(true);
    try {
      const updated = await updateEvent(target, title);
      if (routeId.current === target && generation.current === version) {setEvent(updated); setIsRenaming(false);}
    } catch (error) {
      if (routeId.current === target && generation.current === version) setError(error instanceof Error ? error.message : "Failed to rename event");
    } finally {
      if (routeId.current === target && generation.current === version) {actionBusy.current = false; setBusy(false); setIsSavingTitle(false);}
    }
  };
  const handleConvert = async () => {
    if (!convertTarget || convertTarget.id !== routeId.current || actionBusy.current) return;
    const target = convertTarget.id, version = generation.current;
    actionBusy.current = true; setBusy(true); setIsConverting(true);
    try {
      const album = await convertEventToAlbum(target);
      if (routeId.current === target && generation.current === version) navigate(`/album/${album.id}`);
    } catch (error) {
      if (routeId.current === target && generation.current === version) setError(error instanceof Error ? error.message : "Failed to convert event");
    } finally {
      if (routeId.current === target && generation.current === version) {actionBusy.current = false; setBusy(false); setIsConverting(false); setConvertTarget(null);}
    }
  };

  return (
    <Container maxWidth="xl" sx={{ minHeight: "100vh", py: 4 }}>
      <Box display="flex" alignItems="center" gap={1} mb={1} flexWrap="wrap">
        <TheatersIcon color="primary" />
        {isRenaming ? (
          <>
            <TextField
              size="small"
              value={titleDraft}
              onChange={(e) => setTitleDraft(e.target.value)}
              autoFocus
              disabled={isSavingTitle}
            />
            <Button
              size="small"
              onClick={saveTitle}
              disabled={isSavingTitle || !titleDraft.trim()}
            >
              Save
            </Button>
            <Button
              size="small"
              onClick={() => setIsRenaming(false)}
              disabled={isSavingTitle}
            >
              Cancel
            </Button>
          </>
        ) : (
          <>
            <Typography variant="h5" fontWeight={700}>
              {event?.title || dateRange || "Event"}
            </Typography>
            {event && !config.PRESENTATION_MODE && (
              <IconButton aria-label="Rename event" size="small" onClick={startRenaming}>
                <EditIcon fontSize="small" />
              </IconButton>
            )}
          </>
        )}
        <Box flexGrow={1} />
        {event && !config.PRESENTATION_MODE && (
          <Button
            variant="outlined"
            size="small"
            startIcon={<PhotoAlbumIcon />}
            disabled={busy} onClick={() => setConvertTarget(event)}
          >
            Convert to album
          </Button>
        )}
      </Box>
      <Typography variant="body2" color="text.secondary" mb={3}>
        {event ? `${dateRange} · ${event.media_count} items` : ""}
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      <CursorMediaGrid
        listKey={`event-${eventId}`}
        fetcher={fetcher}
        empty={
          <EmptyState
            icon={<TheatersIcon />}
            title="No media"
            description="This event has no media."
          />
        }
      />

      <ConfirmDialog open={convertTarget !== null} title="Convert event to album?"
        message={`Convert "${convertTarget?.title ?? ""}" to an album and remove this event? This cannot be undone.`}
        confirmLabel="Convert" loading={isConverting} onConfirm={handleConvert} onClose={() => setConvertTarget(null)} />
    </Container>
  );
}
