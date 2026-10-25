import ConfirmDialog from "../components/ConfirmDialog";
import { useUndo, useUndoRefresh } from "../context/UndoContext";
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Container,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Snackbar,
  TextField,
  Tooltip,
  Typography,
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
  const album = loadedAlbum?.id === albumId ? loadedAlbum : null;
  const routeId = useRef(albumId); routeId.current = albumId;
  const generation = useRef(0);
  const actionBusy = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [renameOpen, setRenameOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Album | null>(null);
  const [newName, setNewName] = useState("");
  const [refreshToken, setRefreshToken] = useState(0);
  const [snackbar, setSnackbar] = useState("");

  useEffect(() => {
    const version = ++generation.current;
    const controller = new AbortController();
    const current = () => !controller.signal.aborted && generation.current === version && routeId.current === albumId;
    setAlbum(null); setError(null); actionBusy.current = false; setBusy(false);
    setRenameOpen(false); setDeleteTarget(null);
    if (Number.isFinite(albumId)) getAlbum(albumId, controller.signal)
      .then((data) => { if (current()) { setAlbum(data); setNewName(data.name); } })
      .catch((error) => { if (current()) setError(error instanceof Error ? error.message : "Failed to load album"); });
    return () => { controller.abort(); if (generation.current === version) generation.current += 1; };
  }, [albumId, refreshToken]);

  const fetcher = useCallback(
    (cursor: string | null, signal: AbortSignal) =>
      getAlbumMedia(albumId, cursor, signal),
    [albumId]
  );

  useUndoRefresh(`album-info:${albumId}`, async () => {
    const version = generation.current;
    const result = await getAlbum(albumId);
    if (routeId.current === albumId && generation.current === version) setAlbum(result);
  });

  const handleRename = async () => {
    const trimmed = newName.trim();
    if (!trimmed || !album || actionBusy.current) return;
    const target = album.id, version = generation.current;
    actionBusy.current = true; setBusy(true);
    try {
      const updated = await updateAlbum(target, {name: trimmed});
      if (routeId.current !== target || generation.current !== version) return;
      setAlbum(updated); setRenameOpen(false);
    } catch (error) {
      if (routeId.current === target && generation.current === version) setError(error instanceof Error ? error.message : "Failed to rename album");
    } finally {
      if (routeId.current === target && generation.current === version) {actionBusy.current = false; setBusy(false);}
    }
  };
  const handleDelete = async () => {
    if (!deleteTarget || deleteTarget.id !== routeId.current || actionBusy.current) return;
    const target = deleteTarget.id, version = generation.current;
    actionBusy.current = true; setBusy(true);
    try {
      await deleteAlbum(target);
      if (routeId.current === target && generation.current === version) {setDeleteTarget(null); navigate("/albums");}
    } catch (error) {
      if (routeId.current === target && generation.current === version) setError(error instanceof Error ? error.message : "Failed to delete album");
    } finally {
      if (routeId.current === target && generation.current === version) {actionBusy.current = false; setBusy(false);}
    }
  };

  const handleStartAdding = () => {
    // Jump to the library in select mode; the floating action bar's
    // "Add to Album" finishes the flow.
    navigate("/images", { state: { beginSelection: true, addToAlbumId: albumId } });
  };

  const handleRemoveSelected = async () => {
    if (selectedIds.size === 0 || !album || actionBusy.current) return;
    const target = album.id, version = generation.current;
    actionBusy.current = true; setBusy(true);
    const mediaIds = Array.from(selectedIds);
    try {
      const updated = await removeMediaFromAlbum(
        albumId,
        mediaIds
      );
      if (routeId.current !== target || generation.current !== version) return;
      setAlbum(updated);
      clear();
      setRefreshToken((t) => t + 1);
      setSnackbar("Removed from album.");
      push({
        label: `Removed ${mediaIds.length} item(s) from ${updated.name}`,
        undo: async () => {
          await addMediaToAlbum(albumId, mediaIds);
          await refreshVisible();
        },
      });
    } catch (err) {
      if (routeId.current === target && generation.current === version) setError(err instanceof Error ? err.message : "Failed to remove media");
    } finally {
      if (routeId.current === target && generation.current === version) {actionBusy.current = false; setBusy(false);}
    }
  };

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
          <PhotoAlbumIcon color="primary" />
          <Typography variant="h5" fontWeight={700}>
            {album?.name ?? "Album"}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {album ? `${album.media_count} items` : ""}
          </Typography>
        </Box>
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
            <IconButton color="error" disabled={!album || busy} onClick={() => setDeleteTarget(album)}>
              <DeleteIcon />
            </IconButton>
          </Tooltip>
        </Box>
        )}
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      <CursorMediaGrid
        listKey={`album-${albumId}`}
        fetcher={fetcher}
        refreshToken={refreshToken}
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
            disabled={busy || !newName.trim() || !album}
          >
            Rename
          </Button>
        </DialogActions>
      </Dialog>

      <ConfirmDialog open={deleteTarget !== null} title="Delete album?"
        message={`"${deleteTarget?.name ?? ""}" will be deleted. The media itself stays in your library.`}
        confirmLabel="Delete" loading={busy} onConfirm={handleDelete} onClose={() => setDeleteTarget(null)} />

      <Snackbar
        open={!!snackbar}
        autoHideDuration={3000}
        onClose={() => setSnackbar("")}
        message={snackbar}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      />
    </Container>
  );
}
