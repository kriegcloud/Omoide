import { useUndo } from "../context/UndoContext";
import { getAlbumMemberIds } from "../services/albums";
import React, { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  List,
  ListItemButton,
  ListItemText,
  TextField,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import {
  addMediaToAlbum,
  removeMediaFromAlbum,
  createAlbum,
  getAlbums,
} from "../services/features";
import { Album } from "../types";

interface Props {
  open: boolean;
  mediaIds: number[];
  onClose: () => void;
  onAdded: (album: Album) => void;
}

export const AddToAlbumDialog: React.FC<Props> = ({
  open,
  mediaIds,
  onClose,
  onAdded,
}) => {
  const { push, refreshVisible } = useUndo();
  const [albums, setAlbums] = useState<Album[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(null);
    getAlbums()
      .then(setAlbums)
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Failed to load albums")
      )
      .finally(() => setLoading(false));
  }, [open]);

  const offerUndo = (album: Album, addedIds: number[]) => {
    if (!addedIds.length) return;
    push({
      label: `Added ${addedIds.length} item(s) to ${album.name}`,
      undo: async () => {
        await removeMediaFromAlbum(album.id, addedIds);
        await refreshVisible();
      },
    });
  };

  const addTo = async (albumId: number) => {
    setBusy(true);
    setError(null);
    try {
      const requestedIds = [...mediaIds];
      const existing = await getAlbumMemberIds(albumId);
      const album = await addMediaToAlbum(albumId, requestedIds);
      const addedIds = requestedIds.filter((id) => !existing.has(id));
      offerUndo(album, addedIds);
      onAdded(album);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add media");
    } finally {
      setBusy(false);
    }
  };

  const createAndAdd = async () => {
    const name = newName.trim();
    if (!name) return;
    setBusy(true);
    setError(null);
    try {
      const album = await createAlbum(name);
      const addedIds = [...mediaIds];
      const updated = await addMediaToAlbum(album.id, addedIds);
      offerUndo(updated, addedIds);
      setNewName("");
      onAdded(updated);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create album");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} fullWidth maxWidth="xs">
      <DialogTitle>
        Add {mediaIds.length} item{mediaIds.length === 1 ? "" : "s"} to album
      </DialogTitle>
      <DialogContent dividers>
        {error && (
          <Alert severity="error" sx={{ mb: 1 }}>
            {error}
          </Alert>
        )}
        <Box sx={{ display: "flex", gap: 1, mb: 1.5 }}>
          <TextField
            size="small"
            fullWidth
            placeholder="New album name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void createAndAdd();
            }}
            disabled={busy}
          />
          <Button
            variant="contained"
            disableElevation
            startIcon={<AddIcon />}
            onClick={createAndAdd}
            disabled={busy || !newName.trim()}
          >
            Create
          </Button>
        </Box>
        {loading ? (
          <Box textAlign="center" py={2}>
            <CircularProgress size={24} />
          </Box>
        ) : (
          <List dense>
            {albums.map((album) => (
              <ListItemButton
                key={album.id}
                onClick={() => addTo(album.id)}
                disabled={busy}
              >
                <ListItemText
                  primary={album.name}
                  secondary={`${album.media_count} item${
                    album.media_count === 1 ? "" : "s"
                  }`}
                />
              </ListItemButton>
            ))}
          </List>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          Cancel
        </Button>
      </DialogActions>
    </Dialog>
  );
};
