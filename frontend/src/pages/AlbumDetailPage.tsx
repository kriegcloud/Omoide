import { startOperation, optimisticOperations, projectEntity, acknowledgeQuery, operationVersion } from "../stores/optimisticOperations";
import GalleryPage from "../components/GalleryPage";
import { useMutationRefresh } from "../context/UndoContext";
import ConfirmDialog from "../components/ConfirmDialog";
import { useUndo, useUndoRefresh } from "../context/UndoContext";
import React, { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Snackbar,
  TextField,
  Tooltip,
} from "@mui/material";
import PhotoAlbumIcon from "@mui/icons-material/PhotoAlbum";
import AddPhotoAlternateIcon from "@mui/icons-material/AddPhotoAlternate";
import EditIcon from "@mui/icons-material/Edit";
import DeleteIcon from "@mui/icons-material/Delete";
import RemoveCircleOutlineIcon from "@mui/icons-material/RemoveCircleOutline";
import { useNavigate, useParams } from "react-router-dom";
import { CursorMediaGrid } from "../components/CursorMediaGrid";
import { EmptyState } from "../components/EmptyState";
import { useSelection } from "../context/SelectionContext";
import {
  addMediaToAlbum,
  deleteAlbum,
  getAlbum,
  getAlbumMedia,
  removeMediaFromAlbum,
  updateAlbum,
} from "../services/features";
import { Album } from "../types";
import config from "../config";

export default function AlbumDetailPage() {
  const { id } = useParams<{ id: string }>();
  const albumId = Number(id);
  const navigate = useNavigate();
  const { push, refreshVisible } = useUndo();
  const { selectedIds, clear, isSelecting } = useSelection();

  const [loadedAlbum, setAlbum] = useState<Album | null>(null);
  useSyncExternalStore(optimisticOperations.subscribe, optimisticOperations.getSnapshot, optimisticOperations.getSnapshot);
  const album = loadedAlbum?.id === albumId ? projectEntity(`album:${albumId}`, loadedAlbum, `album-info:${albumId}`) : null;
  const routeId = useRef(albumId); routeId.current = albumId;
  const generation = useRef(0);
  const [error, setError] = useState<string | null>(null);
  const [renameOpen, setRenameOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Album | null>(null);
  const [newName, setNewName] = useState("");
  const [snackbar, setSnackbar] = useState("");

  const metadataRequest = useRef(0);
  useMutationRefresh(["album:changed", "media:deleted", "media:updated", "media:moved", "list:invalidate"], async () => {
    const fence = operationVersion();
    const request = ++metadataRequest.current;
    const version = generation.current;
    try {
      const data = await getAlbum(albumId);
      if (request === metadataRequest.current && generation.current === version && routeId.current === albumId) { setAlbum(data); acknowledgeQuery(`album-info:${albumId}`, fence); }
    } catch (error) {
      if (request === metadataRequest.current && generation.current === version && routeId.current === albumId) setError(error instanceof Error ? error.message : "Failed to refresh album");
    }
  });

  useEffect(() => {
    const version = ++generation.current;
    const fence = operationVersion();
    const request = ++metadataRequest.current;
    const controller = new AbortController();
    const current = () => request === metadataRequest.current && !controller.signal.aborted && generation.current === version && routeId.current === albumId;
    setAlbum(null); setError(null);
    setRenameOpen(false); setDeleteTarget(null);
    if (Number.isFinite(albumId)) getAlbum(albumId, controller.signal)
      .then((data) => { if (current()) { setAlbum(data); acknowledgeQuery(`album-info:${albumId}`, fence); setNewName(data.name); } })
      .catch((error) => { if (current()) setError(error instanceof Error ? error.message : "Failed to load album"); });
    return () => { controller.abort(); if (generation.current === version) generation.current += 1; };
  }, [albumId]);

  const fetcher = useCallback(
    (cursor: string | null, signal: AbortSignal) =>
      getAlbumMedia(albumId, cursor, signal),
    [albumId]
  );

  useUndoRefresh(`album-info:${albumId}`, async () => {
    const fence = operationVersion();
    const request = ++metadataRequest.current;
    const version = generation.current;
    const result = await getAlbum(albumId);
    if (routeId.current === albumId && generation.current === version && request === metadataRequest.current) { setAlbum(result); acknowledgeQuery(`album-info:${albumId}`, fence); }
  });

  const handleRename = () => {
    const trimmed = newName.trim();
    if (!trimmed || !album) return;
    const target = album.id, version = generation.current;
    setRenameOpen(false);
    startOperation({
      label: `Rename album to ${trimmed}`, input: { target, name: trimmed },
      resources: [`album:${target}`],
      patches: [{ kind: "fields", key: `album:${target}`, values: { name: trimmed } }],
      request: async input => {
        const result = await updateAlbum(input.target, { name: input.name });
        if (routeId.current === target && generation.current === version) setAlbum(result);
        return result;
      },
    });
  };
  const handleDelete = () => {
    if (!deleteTarget || deleteTarget.id !== routeId.current) return;
    const target = deleteTarget.id;
    setDeleteTarget(null);
    startOperation({
      label: `Delete album ${deleteTarget.name}`, input: { target },
      resources: [`album:${target}`],
      patches: [{ kind: "remove", key: `album:${target}` }],
      request: input => deleteAlbum(input.target),
    });
    navigate("/albums");
  };

  const handleStartAdding = () => {
    // Jump to the library in select mode; the floating action bar's
    // "Add to Album" finishes the flow.
    navigate("/images", { state: { beginSelection: true, addToAlbumId: albumId } });
  };

  const handleRemoveSelected = () => {
    if (selectedIds.size === 0 || !album) return;
    const target = album.id, version = generation.current;
    const mediaIds = Array.from(selectedIds);
    clear();
    startOperation({
      label: `Remove items from ${album.name}`,
      input: { target, mediaIds },
      resources: [`album:${target}`, ...mediaIds.map(id => `media:${id}` as const)],
      patches: mediaIds.map(id => ({ kind: "remove" as const, key: `media:${id}` as const, query: `album-${target}` })),
      request: async (input, operationId) => {
        const updated = await removeMediaFromAlbum(input.target, input.mediaIds);
        if (routeId.current === target && generation.current === version) {
          setAlbum(updated);
          setSnackbar(`Removed ${updated.removed_ids.length} item(s) from album.`);
        }
        if (updated.removed_ids.length) push({
          label: `Removed ${updated.removed_ids.length} item(s) from ${updated.name}`,
          operationId,
          undo: async () => {
            const result = await startOperation({
              label: `Undo removing items from ${updated.name}`,
              input: { target: input.target, ids: updated.removed_ids },
              resources: [`album:${input.target}`, ...updated.removed_ids.map(id => `media:${id}` as const)], patches: [],
              request: inverse => addMediaToAlbum(inverse.target, inverse.ids), refresh: refreshVisible,
            }).done;
            if (result.state !== "committed") throw new Error(result.error ?? "Album Undo needs reconciliation");
          },
        });
        return { updated, operationId };
      },
      receipt: ({ updated, operationId }) => ({
        operation_id: operationId,
        state: updated.skipped_ids.length ? (updated.removed_ids.length ? "partial" : "failed") : "succeeded",
        applied: updated.removed_ids.map(id => `media:${id}`),
        skipped: updated.skipped_ids.map(id => ({ key: `media:${id}`, reason: "not_a_member" })),
        failed: [], unknown: [],
      }),
    });
  };

  return (
    <GalleryPage title={album?.name ?? "Album"} count={album?.media_count}
      actions={      <Box
        display="flex"
        justifyContent="space-between"
        alignItems="center"
        flexWrap="wrap"
        gap={1}

      >

        {!config.PRESENTATION_MODE && (
        <Box display="flex" gap={1}>
          <Button
            size="small"
            variant="contained"
            disableElevation
            startIcon={<AddPhotoAlternateIcon />}
            onClick={handleStartAdding}
          >
            Add media
          </Button>
          {isSelecting && selectedIds.size > 0 && (
            <Button
              size="small"
              color="warning"
              variant="outlined"
              startIcon={<RemoveCircleOutlineIcon />}
              onClick={handleRemoveSelected}
            >
              Remove {selectedIds.size} from album
            </Button>
          )}
          <Tooltip title="Rename album">
            <IconButton onClick={() => setRenameOpen(true)}>
              <EditIcon />
            </IconButton>
          </Tooltip>
          <Tooltip title="Delete album (keeps the media)">
            <IconButton color="error" disabled={!album} onClick={() => setDeleteTarget(album)}>
              <DeleteIcon />
            </IconButton>
          </Tooltip>
        </Box>
        )}
      </Box>}>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      <CursorMediaGrid
        listKey={`album-${albumId}`}
        fetcher={fetcher}
        empty={
          <EmptyState
            icon={<PhotoAlbumIcon />}
            title="Album is empty"
            description={
              config.PRESENTATION_MODE
                ? undefined
                : "Click “Add media” above (or use Select Mode in any grid), pick items, then “Add to Album”."
            }
          />
        }
      />

      <Dialog
        open={renameOpen}
        onClose={() => setRenameOpen(false)}
        fullWidth
        maxWidth="xs"
      >
        <DialogTitle>Rename album</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            size="small"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleRename();
            }}
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRenameOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            disableElevation
            onClick={handleRename}
            disabled={!newName.trim() || !album}
          >
            Rename
          </Button>
        </DialogActions>
      </Dialog>

      <ConfirmDialog open={deleteTarget !== null} title="Delete album?"
        message={`"${deleteTarget?.name ?? ""}" will be deleted. The media itself stays in your library.`}
        confirmLabel="Delete" loading={false} onConfirm={handleDelete} onClose={() => setDeleteTarget(null)} />

      <Snackbar
        open={!!snackbar}
        autoHideDuration={3000}
        onClose={() => setSnackbar("")}
        message={snackbar}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      />
    </GalleryPage>
  );
}
