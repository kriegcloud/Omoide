import React, { useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardActionArea,
  CardContent,
  Checkbox,
  CircularProgress,
  Container,
  Snackbar,
  Typography,
} from "@mui/material";
import TheatersIcon from "@mui/icons-material/Theaters";
import AutorenewIcon from "@mui/icons-material/Autorenew";
import { Link } from "react-router-dom";
import { useInView } from "react-intersection-observer";
import config, { API } from "../config";
import { encodeFilePath } from "../urlUtils";
import { EmptyState } from "../components/EmptyState";
import { getEvents, startBuildEvents } from "../services/features";
import { useTaskCompletionVersion } from "../TaskEventsContext";
import { EventItem } from "../types";
import ConfirmDialog from "../components/ConfirmDialog";
import MarqueeSelectionBox from "../components/MarqueeSelectionBox";
import { useEntitySelection } from "../hooks/useEntitySelection";
import { useMarqueeSelection } from "../hooks/useMarqueeSelection";
import { deleteEventsBulk } from "../services/events";

const formatRange = (startIso: string, endIso: string) => {
  const start = new Date(startIso);
  const end = new Date(endIso);
  const opts: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" };
  const startStr = start.toLocaleDateString(undefined, opts);
  const endStr = end.toLocaleDateString(undefined, opts);
  const year = end.getFullYear();
  return startStr === endStr
    ? `${startStr}, ${year}`
    : `${startStr} – ${endStr}, ${year}`;
};

export default function EventsPage() {
  const { ref: loaderRef, inView } = useInView({ threshold: 0.5 });
  const [items, setItems] = useState<EventItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [snackbar, setSnackbar] = useState("");
  const refreshKey = useTaskCompletionVersion(["build_events"]);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const gridRef = useRef<HTMLDivElement>(null);
  const selection = useEntitySelection<number>();
  const { marqueeRect, onItemClick } = useMarqueeSelection<number>({
    containerRef: gridRef,
    itemSelector: "[data-selectable-id]",
    getId: (element) => Number(element.dataset.selectableId),
    enabled: selection.selectionMode,
    selectedIds: selection.selectedIds,
    onSelectionChange: selection.setSelected,
  });

  const loadPage = async (fromCursor: string | null, replace: boolean) => {
    setIsLoading(true);
    setError(null);
    try {
      const page = await getEvents(fromCursor);
      setItems((prev) => (replace ? page.items : [...prev, ...page.items]));
      setCursor(page.next_cursor);
      setHasMore(page.next_cursor !== null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load events");
      setHasMore(false);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    setItems([]);
    setCursor(null);
    setHasMore(true);
    void loadPage(null, true);
  }, [refreshKey]);

  useEffect(() => {
    if (inView && hasMore && !isLoading && !error) {
      void loadPage(cursor, false);
    }
  }, [inView, hasMore, isLoading, error, cursor]);

  useEffect(() => {
    selection.pruneTo(items.map((item) => item.id));
  }, [items, selection.pruneTo]);

  const handleRebuild = async () => {
    try {
      await startBuildEvents();
      setSnackbar("Event clustering started — this page refreshes when done.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start task");
    }
  };

  const handleDeleteSelected = async () => {
    setIsDeleting(true);
    try {
      const result = await deleteEventsBulk(Array.from(selection.selectedIds));
      const deleted = new Set(result.deleted_ids);
      setItems((previous) => previous.filter((item) => !deleted.has(item.id)));
      selection.toggleMode();
      setDeleteOpen(false);
      setSnackbar(
        `Deleted ${result.deleted_ids.length} event${result.deleted_ids.length === 1 ? "" : "s"}.`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete events");
    } finally {
      setIsDeleting(false);
    }
  };

  if (!config.EVENTS_ENABLED) {
    return (
      <Typography variant="h5" color="text.primary" gutterBottom>
        Events disabled!
      </Typography>
    );
  }

  return (
    <Container maxWidth="xl" sx={{ minHeight: "100vh", py: 4 }}>
      <Box
        display="flex"
        justifyContent="space-between"
        alignItems="center"
        flexWrap="wrap"
        gap={1}
        mb={3}
      >
        <Box display="flex" alignItems="center" gap={1}>
          <TheatersIcon color="primary" />
          <Typography variant="h5" fontWeight={700}>
            Events
          </Typography>
        </Box>
        <Box display="flex" gap={1} alignItems="center">
          {selection.selectionMode && (
            <Typography variant="body2" color="text.secondary">
              {selection.selectedIds.size} selected
            </Typography>
          )}
          <Button variant="outlined" size="small" onClick={selection.toggleMode}>
            {selection.selectionMode ? "Cancel Selection" : "Select Events"}
          </Button>
          {selection.selectionMode && (
            <Button
              variant="contained"
              color="error"
              size="small"
              disabled={selection.selectedIds.size === 0 || isDeleting}
              onClick={() => setDeleteOpen(true)}
            >
              Delete Selected
            </Button>
          )}
          <Button
            variant="outlined"
            startIcon={<AutorenewIcon />}
            onClick={handleRebuild}
          >
            Rebuild events
          </Button>
        </Box>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {items.length === 0 && !isLoading && !error && (
        <EmptyState
          icon={<TheatersIcon />}
          title="No events yet"
          description="Run “Rebuild events” to cluster your library into trips and moments. Run geocoding first (Places page) for named events."
        />
      )}

      <Box
        ref={gridRef}
        sx={{
          display: "grid",
          gap: 2,
          gridTemplateColumns: {
            xs: "repeat(1, 1fr)",
            sm: "repeat(2, 1fr)",
            md: "repeat(3, 1fr)",
            lg: "repeat(4, 1fr)",
          },
          position: "relative",
        }}
      >
        {items.map((event) => (
          <Card
            key={event.id}
            data-selectable-id={event.id}
            sx={{
              borderRadius: 3,
              position: "relative",
              outline: selection.selectedIds.has(event.id) ? "3px solid" : "none",
              outlineColor: "primary.main",
            }}
          >
            <CardActionArea
              component={Link}
              to={`/event/${event.id}`}
              onClick={
                selection.selectionMode
                  ? (clickEvent) => onItemClick(event.id, clickEvent)
                  : undefined
              }
            >
              <Box
                sx={{
                  aspectRatio: "16/9",
                  bgcolor: "action.hover",
                  overflow: "hidden",
                }}
              >
                {event.cover_thumbnail && (
                  <Box
                    component="img"
                    src={`${API}/thumbnails/${encodeFilePath(
                      event.cover_thumbnail
                    )}`}
                    alt={event.title ?? "Event"}
                    loading="lazy"
                    sx={{ width: "100%", height: "100%", objectFit: "cover" }}
                  />
                )}
              </Box>
              <CardContent sx={{ py: 1.5 }}>
                <Typography variant="subtitle2" fontWeight={700} noWrap>
                  {event.title || formatRange(event.start_at, event.end_at)}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {event.title
                    ? `${formatRange(event.start_at, event.end_at)} · `
                    : ""}
                  {event.media_count} item{event.media_count === 1 ? "" : "s"}
                </Typography>
              </CardContent>
            </CardActionArea>
            {selection.selectionMode && (
              <Checkbox
                checked={selection.selectedIds.has(event.id)}
                size="small"
                sx={{ position: "absolute", top: 4, left: 4, pointerEvents: "none" }}
              />
            )}
          </Card>
        ))}
        <MarqueeSelectionBox container={gridRef.current} rect={marqueeRect} />
      </Box>

      {isLoading && (
        <Box textAlign="center" py={3}>
          <CircularProgress />
        </Box>
      )}
      {hasMore && !error && <Box ref={loaderRef} sx={{ height: 10 }} />}

      <ConfirmDialog
        open={deleteOpen}
        title="Delete Selected Events"
        message={`Delete ${selection.selectedIds.size} selected event${selection.selectedIds.size === 1 ? "" : "s"}? The media itself will stay in your library.`}
        confirmLabel="Delete"
        loading={isDeleting}
        onConfirm={handleDeleteSelected}
        onClose={() => setDeleteOpen(false)}
      />

      <Snackbar
        open={!!snackbar}
        autoHideDuration={4000}
        onClose={() => setSnackbar("")}
        message={snackbar}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      />
    </Container>
  );
}
