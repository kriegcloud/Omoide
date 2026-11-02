import { useUndo, useMutationRefresh } from "../context/UndoContext";
import { startOperation } from "../stores/optimisticOperations";
import React, { useEffect, useState, useRef } from "react";
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
  const submitting = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");

  const [revision, setRevision] = useState(0);
  useMutationRefresh(["album:changed", "media:deleted", "media:updated", "media:moved", "list:invalidate"], () => { if (open) setRevision(value => value + 1); });

  useEffect(() => {
    let active = true;
    if (!open) return;
    setLoading(true);
    setError(null);
    getAlbums()
      .then(data => { if (active) setAlbums(data); })
      .catch((err) =>
        active && setError(err instanceof Error ? err.message : "Failed to load albums")
      )
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [open, revision]);

  const offerUndo = (album: Album, addedIds: number[], operationId: string) => {
    if (!addedIds.length) return;
    push({
      label: `Added ${addedIds.length} item(s) to ${album.name}`,
      operationId,
      undo: async () => {
        const result = await startOperation({
          label: `Undo adding items to ${album.name}`, input: { id: album.id, ids: addedIds },
          resources: [`album:${album.id}`, ...addedIds.map(id => `media:${id}` as const)], patches: [],
          request: input => removeMediaFromAlbum(input.id, input.ids), refresh: refreshVisible,
        }).done;
        if (result.state !== "committed") throw new Error(result.error ?? "Album Undo needs reconciliation");
      },
    });
  };

  const submit = (albumId?: number) => {
    const name = newName.trim();
    if (submitting.current || (albumId === undefined && !name)) return;
    submitting.current = true;
    const requestedIds = [...new Set(mediaIds)];
    setBusy(true);
    onClose();
    const operation = startOperation({
      label: albumId === undefined ? `Create album ${name} and add items` : "Add items to album",
      input: { albumId, name, requestedIds },
      resources: [`album:${albumId ?? `new:${name}`}`, ...requestedIds.map(id => `media:${id}` as const)],
      patches: [],
      request: async (input, operationId) => {
        // A created album is retained if membership fails: it may already be shared.
        const target = input.albumId ?? (await createAlbum(input.name)).id;
        const album = await addMediaToAlbum(target, input.requestedIds);
        offerUndo(album, album.added_ids, operationId);
        onAdded(album);
        return { album, operationId };
      },
      receipt: ({ album, operationId }) => ({
        operation_id: operationId, state: album.skipped_ids.length ? (album.added_ids.length ? "partial" : "failed") : "succeeded",
        applied: album.added_ids.map(id => `media:${id}`),
        skipped: album.skipped_ids.map(id => ({ key: `media:${id}`, reason: "already_member_or_missing" })), failed: [], unknown: [],
      }),
    });
    void operation.done.then(() => { submitting.current = false; setBusy(false); });
    setNewName("");
  };
  const addTo = (albumId: number) => submit(albumId);
  const createAndAdd = () => submit();

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
