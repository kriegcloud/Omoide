import { startOperation, optimisticOperations, projectList, acknowledgeQuery, operationVersion } from "../stores/optimisticOperations";
import GalleryPage from "../components/GalleryPage";
import Gallery from "../components/Gallery";
import { useMutationRefresh } from "../context/UndoContext";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
  Typography,
} from "@mui/material";
import PhotoAlbumIcon from "@mui/icons-material/PhotoAlbum";
import AddIcon from "@mui/icons-material/Add";
import AlbumCard from "../components/AlbumCard";
import { EmptyState } from "../components/EmptyState";
import { createAlbum, getAlbums } from "../services/features";
import { Album } from "../types";
import ConfirmDialog from "../components/ConfirmDialog";
import MarqueeSelectionBox from "../components/MarqueeSelectionBox";
import { useEntitySelection } from "../hooks/useEntitySelection";
import { useGridSelection } from "../hooks/useMarqueeSelection";
import { deleteAlbumsBulk } from "../services/albums";

export default function AlbumsPage() {
  const [baseAlbums, setAlbums] = useState<Album[]>([]);
  useSyncExternalStore(optimisticOperations.subscribe, optimisticOperations.getSnapshot, optimisticOperations.getSnapshot);
  const [pendingAlbums, setPendingAlbums] = useState<Album[]>([]);
  const creating = useRef(false);
  const nextTemporaryId = useRef(-1);
  const albums = projectList("albums", "album", [...pendingAlbums, ...baseAlbums]).items;
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [deleteTargets, setDeleteTargets] = useState<number[] | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const gridRef = useRef<HTMLDivElement>(null);
  const selection = useEntitySelection<number>();
  const {pruneTo} = selection;
  const { marqueeRect, onItemClick } = useGridSelection<number>({
    containerRef: gridRef,
    selecting: selection.selectionMode,
    onEnterSelection: selection.enterMode,
    onExitSelection: selection.exitMode,
    selectedIds: selection.selectedIds,
    onSelectionChange: selection.setSelected,
  });

  const requestVersion = useRef(0);
  const load = useCallback(async () => {
    const version = ++requestVersion.current;
    const fence = operationVersion();
    setIsLoading(true); setError(null);
    try {
      const albums = await getAlbums();
      if (version === requestVersion.current) { setAlbums(albums); acknowledgeQuery("albums", fence); }
    } catch (err) {
      if (version === requestVersion.current) setError(err instanceof Error ? err.message : "Failed to load albums");
    } finally {
      if (version === requestVersion.current) setIsLoading(false);
    }
  }, []);
  useMutationRefresh(["album:changed", "media:deleted", "media:updated", "media:moved", "list:invalidate"], load);
  useEffect(() => {
    void load();
    return () => { requestVersion.current += 1; };
  }, [load]);

  useEffect(() => {
    pruneTo(albums.map((album) => album.id));
  }, [albums, pruneTo]);

  const handleCreate = () => {
    const trimmed = name.trim();
    if (!trimmed || creating.current) return;
    creating.current = true;
    const provisional: Album = { id: nextTemporaryId.current--, name: trimmed, description: null,
      created_at: new Date().toISOString(), media_count: 0, cover_thumbnail: null };
    setPendingAlbums(previous => [provisional, ...previous]);
    setCreateOpen(false); setName(""); setBusy(true);
    const operation = startOperation({
      label: `Create album ${trimmed}`, input: { name: trimmed },
      resources: [`album:new:${trimmed}`], patches: [],
      request: async input => {
        const created = await createAlbum(input.name);
        setAlbums(previous => [created, ...previous.filter(row => row.id !== created.id)]);
        setPendingAlbums(previous => previous.filter(row => row.id !== provisional.id));
        return created;
      },
    });
    void operation.done.then(record => {
      if (record.state === "rejected") setPendingAlbums(previous => previous.filter(row => row.id !== provisional.id));
      creating.current = false; setBusy(false);
    });
  };

  const handleDeleteSelected = () => {
    if (!deleteTargets?.length) return;
    const ids = [...deleteTargets];
    setDeleteTargets(null);
    setIsDeleting(true);
    const operation = startOperation({
      label: "Delete selected albums",
      input: { ids }, resources: ids.map(id => `album:${id}` as const),
      patches: ids.map(id => ({ kind: "remove" as const, key: `album:${id}` as const, query: "albums" })),
      request: async (input, operationId) => {
        const result = await deleteAlbumsBulk(input.ids);
        setAlbums(previous => previous.filter(album => !result.deleted_ids.includes(album.id)));
        return { result, operationId };
      },
      receipt: ({ result, operationId }) => ({
        operation_id: operationId, state: result.skipped_ids.length ? "partial" : "succeeded",
        applied: result.deleted_ids.map(id => `album:${id}`),
        skipped: result.skipped_ids.map(id => ({ key: `album:${id}`, reason: "not_found" })), failed: [], unknown: [],
      }),
    });
    void operation.done.then(() => setIsDeleting(false));
  };

  return (
    <GalleryPage title="Albums" count={albums.length}>
      <Box
        display="flex"
        justifyContent="space-between"
        alignItems="center"
        flexWrap="wrap"
        gap={1}
        mb={3}
      >
        <Box display="flex" alignItems="center" gap={1}>
          <PhotoAlbumIcon color="primary" />

        </Box>
        <Box display="flex" gap={1} alignItems="center">
          {selection.selectionMode && (
            <Typography variant="body2" color="text.secondary">
              {selection.selectedIds.size} selected
            </Typography>
          )}
          <Button variant="outlined" size="small" onClick={selection.toggleMode}>
            {selection.selectionMode ? "Cancel Selection" : "Select Albums"}
          </Button>
          {selection.selectionMode && (
            <Button
              variant="contained"
              color="error"
              size="small"
              disabled={selection.selectedIds.size === 0 || isDeleting}
              onClick={() => setDeleteTargets(Array.from(selection.selectedIds))}
            >
              Delete Selected
            </Button>
          )}
          <Button
            variant="contained"
            disableElevation
            startIcon={<AddIcon />}
            onClick={() => setCreateOpen(true)}
          >
            New Album
          </Button>
        </Box>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {isLoading && albums.length === 0 ? (
        <Box textAlign="center" py={6}>
          <CircularProgress />
        </Box>
      ) : albums.length === 0 ? (
        <EmptyState
          icon={<PhotoAlbumIcon />}
          title="No albums yet"
          description="Create an album, then add media via Select Mode."
        />
      ) : (
        <Box ref={gridRef} sx={{ position: "relative" }}><Gallery>
          {albums.map((album) => (
            album.id < 0 ? <Box key={album.id} role="status" sx={{ p: 2, color: "text.secondary" }}>
              <CircularProgress size={16} sx={{ mr: 1 }} />{album.name} — Saving…
            </Box> : <AlbumCard
              key={album.id}
              album={album}
              selecting={selection.selectionMode}
              selected={selection.selectedIds.has(album.id)}
              onSelectionClick={onItemClick}
            />
          ))}

        </Gallery><MarqueeSelectionBox container={gridRef.current} rect={marqueeRect} /></Box>
      )}

      <Dialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        fullWidth
        maxWidth="xs"
      >
        <DialogTitle>New album</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            size="small"
            placeholder="Album name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleCreate();
            }}
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreateOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            disableElevation
            onClick={handleCreate}
            disabled={busy || !name.trim()}
          >
            Create
          </Button>
        </DialogActions>
      </Dialog>
      <ConfirmDialog
        open={deleteTargets !== null}
        title="Delete Selected Albums"
        message={`Delete ${deleteTargets?.length ?? 0} selected album${deleteTargets?.length === 1 ? "" : "s"}? The media itself will stay in your library.`}
        confirmLabel="Delete"
        loading={isDeleting}
        onConfirm={handleDeleteSelected}
        onClose={() => setDeleteTargets(null)}
      />
    </GalleryPage>
  );
}
