import { startGeographyMutation } from '../services/geographyOperations';
import { optimisticOperations, projectEntity, operationVersion, acknowledgeQuery } from '../stores/optimisticOperations';
import GalleryPage from "../components/GalleryPage";
import { useMutationRefresh } from "../context/UndoContext";
import ConfirmDialog from "../components/ConfirmDialog";
import React, { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  Alert,
  Box,
  Button,
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
  getEvents,
  getEventMedia,
  updateEvent,
} from "../services/features";
import { EventItem } from "../types";

export default function EventDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const eventId = Number(id);
  const [loadedEvent, setEvent] = useState<EventItem | null>(null);
  useSyncExternalStore(optimisticOperations.subscribe, optimisticOperations.getSnapshot, optimisticOperations.getSnapshot);
  const event = loadedEvent?.id === eventId ? projectEntity(`event:${eventId}`, loadedEvent, `event-detail:${eventId}`) : null;
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

  const metadataRequest = useRef(0);
  useMutationRefresh(["events:changed", "media:deleted", "media:updated", "media:moved", "list:invalidate"], async () => {
    const request = ++metadataRequest.current;
    const version = generation.current;
    try {
      const data = await getEvent(eventId);
      if (request === metadataRequest.current && generation.current === version && routeId.current === eventId) setEvent(data);
    } catch (error) {
      if (request === metadataRequest.current && generation.current === version && routeId.current === eventId) setError(error instanceof Error ? error.message : "Failed to refresh event");
    }
  });

  useEffect(() => {
    const version = ++generation.current;
    const request = ++metadataRequest.current;
    const controller = new AbortController();
    const current = () => request === metadataRequest.current && !controller.signal.aborted && generation.current === version && routeId.current === eventId;
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

  const refreshEvent = async () => {
    const target = eventId;
    const started = operationVersion();
    const updated = await getEvent(target);
    if (routeId.current === target) setEvent(updated);
    acknowledgeQuery(`event-detail:${target}`, started);
  };
  const saveTitle = () => {
    const title = titleDraft.trim();
    if (!title || !event) return;
    const target = event.id;
    const key = `event:${target}` as const;
    setIsRenaming(false);
    startGeographyMutation("Rename event", key, [{kind: "fields", key, values: {title}}], () => updateEvent(target, title), refreshEvent);
  };
  const handleConvert = () => {
    if (!convertTarget || convertTarget.id !== routeId.current || actionBusy.current) return;
    const target = convertTarget.id, version = generation.current;
    const key = `event:${target}` as const;
    setConvertTarget(null);
    actionBusy.current = true; setBusy(true);
    const handle = startGeographyMutation("Convert event to album", key, [{kind: "derived", key, field: "conversion"}], async () => {
      const album = await convertEventToAlbum(target);
      if (routeId.current === target && generation.current === version) navigate(`/album/${album.id}`);
      return album;
    }, async () => {
      // Conversion removes the event. Revalidate the collection, including lost responses.
      await getEvents(null);
    });
    void handle.done.then(() => {
      if (routeId.current === target && generation.current === version) { actionBusy.current = false; setBusy(false); }
    });
  };

  return (
    <GalleryPage title={event?.title || dateRange || "Event"} count={event?.media_count}
      actions={      <Box display="flex" alignItems="center" gap={1}  flexWrap="wrap">

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

            {event && !config.PRESENTATION_MODE && (
              <IconButton aria-label="Rename event" size="small" onClick={startRenaming}>
                <EditIcon fontSize="small" />
              </IconButton>
            )}
          </>
        )}

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
      </Box>}>

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
    </GalleryPage>
  );
}
