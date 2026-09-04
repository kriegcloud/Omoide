import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardActionArea,
  CardContent,
  CircularProgress,
  Checkbox,
  Container,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
  Typography,
} from "@mui/material";
import PhotoAlbumIcon from "@mui/icons-material/PhotoAlbum";
import AddIcon from "@mui/icons-material/Add";
import { Link } from "react-router-dom";
import { API } from "../config";
import { encodeFilePath } from "../urlUtils";
import { EmptyState } from "../components/EmptyState";
import { createAlbum, getAlbums } from "../services/features";
import { Album } from "../types";
import ConfirmDialog from "../components/ConfirmDialog";
import MarqueeSelectionBox from "../components/MarqueeSelectionBox";
import { useEntitySelection } from "../hooks/useEntitySelection";
import { useMarqueeSelection } from "../hooks/useMarqueeSelection";
import { deleteAlbumsBulk } from "../services/albums";

export default function AlbumsPage() {
  const [albums, setAlbums] = useState<Album[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
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

  const load = useCallback(() => {
    setIsLoading(true);
    getAlbums()
      .then(setAlbums)
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Failed to load albums")
      )
      .finally(() => setIsLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    selection.pruneTo(albums.map((album) => album.id));
  }, [albums, selection.pruneTo]);

  const handleCreate = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setBusy(true);
    try {
      await createAlbum(trimmed);
      setName("");
      setCreateOpen(false);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create album");
    } finally {
      setBusy(false);
    }
  };

  const handleDeleteSelected = async () => {
    setIsDeleting(true);
    try {
      const result = await deleteAlbumsBulk(Array.from(selection.selectedIds));
      const deleted = new Set(result.deleted_ids);
      setAlbums((previous) => previous.filter((album) => !deleted.has(album.id)));
      selection.toggleMode();
      setDeleteOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete albums");
    } finally {
      setIsDeleting(false);
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
            Albums
          </Typography>
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
              onClick={() => setDeleteOpen(true)}
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

      {isLoading ? (
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
        <Box
          ref={gridRef}
          sx={{
            display: "grid",
            gap: 2,
            gridTemplateColumns: {
              xs: "repeat(2, 1fr)",
              sm: "repeat(3, 1fr)",
              md: "repeat(4, 1fr)",
              lg: "repeat(5, 1fr)",
            },
            position: "relative",
          }}
        >
          {albums.map((album) => (
            <Card
              key={album.id}
              data-selectable-id={album.id}
              sx={{
                borderRadius: 3,
                position: "relative",
                outline: selection.selectedIds.has(album.id) ? "3px solid" : "none",
                outlineColor: "primary.main",
              }}
            >
              <CardActionArea
                component={Link}
                to={`/album/${album.id}`}
                onClick={
                  selection.selectionMode
                    ? (event) => onItemClick(album.id, event)
                    : undefined
                }
              >
                <Box
                  sx={{
                    aspectRatio: "4/3",
                    bgcolor: "action.hover",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    overflow: "hidden",
                  }}
                >
                  {album.cover_thumbnail ? (
                    <Box
                      component="img"
                      src={`${API}/thumbnails/${encodeFilePath(
                        album.cover_thumbnail
                      )}`}
                      alt={album.name}
                      loading="lazy"
                      sx={{
                        width: "100%",
                        height: "100%",
                        objectFit: "cover",
                      }}
                    />
                  ) : (
                    <PhotoAlbumIcon color="disabled" sx={{ fontSize: 48 }} />
                  )}
                </Box>
                <CardContent sx={{ py: 1.5 }}>
                  <Typography variant="subtitle2" fontWeight={700} noWrap>
                    {album.name}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {album.media_count} item
                    {album.media_count === 1 ? "" : "s"}
                  </Typography>
                </CardContent>
              </CardActionArea>
              {selection.selectionMode && (
                <Checkbox
                  checked={selection.selectedIds.has(album.id)}
                  size="small"
                  sx={{ position: "absolute", top: 4, left: 4, pointerEvents: "none" }}
                />
              )}
            </Card>
          ))}
          <MarqueeSelectionBox container={gridRef.current} rect={marqueeRect} />
        </Box>
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
        open={deleteOpen}
        title="Delete Selected Albums"
        message={`Delete ${selection.selectedIds.size} selected album${selection.selectedIds.size === 1 ? "" : "s"}? The media itself will stay in your library.`}
        confirmLabel="Delete"
        loading={isDeleting}
        onConfirm={handleDeleteSelected}
        onClose={() => setDeleteOpen(false)}
      />
    </Container>
  );
}
