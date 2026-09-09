import React, { useState } from "react";
import { Box, Typography, IconButton, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle, Button, Snackbar, Alert } from "@mui/material";
import { alpha } from "@mui/material/styles";
import MovieIcon from "@mui/icons-material/Movie";
import PersonIcon from "@mui/icons-material/Person";
import DeleteIcon from "@mui/icons-material/Delete";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import { Tag } from "../types";
import { deleteTag } from "../services/tagActions";
import { API } from "../config";
import { encodeFilePath } from "../urlUtils";
import type { SelectionClickEvent } from "../hooks/useMarqueeSelection";
import SelectableTileFrame from "./SelectableTileFrame";
interface TagCardProps {
  tag: Tag;
  onTagDeleted: (tagId: number) => void;
  selecting?: boolean;
  selected?: boolean;
  onSelectionClick?: (tagId: number, event: SelectionClickEvent) => boolean;
}

export default function TagCard({
  tag,
  onTagDeleted,
  selecting = false,
  selected = false,
  onSelectionClick,
}: TagCardProps) {
  const [openConfirmDialog, setOpenConfirmDialog] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleOpenConfirmDialog = (event: React.MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    setOpenConfirmDialog(true);
  };

  const handleCloseConfirmDialog = () => {
    setOpenConfirmDialog(false);
  };

  const handleConfirmDelete = async () => {
    handleCloseConfirmDialog();
    try {
      await deleteTag(tag.id);
      onTagDeleted(tag.id);
    } catch (error) {
      console.error("Error during tag deletion:", error);
      setErrorMessage(`An error occurred while deleting tag "${tag.name}".`);
    }
  };

  // --- Logic to create a mixed list of media and person thumbnails ---
  const mediaPreviews = tag.media.slice(0, 4).map((m) => ({
    type: "media",
    id: m.id,
    url: `${API}/thumbnails/${m.thumbnail_path ? encodeFilePath(m.thumbnail_path) : `${m.id}.jpg`}`,
  }));

  const personPreviews = tag.persons
    .filter((p) => p.profile_face?.thumbnail_path)
    .slice(0, 4)
    .map((p) => ({
      type: "person",
      id: p.id,
      url: `${API}/thumbnails/${encodeFilePath(p.profile_face!.thumbnail_path!)}`,
    }));

  // Combine and slice to ensure we have a max of 4 total previews for the collage
  const previewItems = [...mediaPreviews, ...personPreviews].slice(0, 4);

  return (
    <>
      <SelectableTileFrame
        id={tag.id}
        href={`/tag/${tag.id}`}
        aspectRatio="1/1"
        selecting={selecting}
        selected={selected}
        onSelectionClick={onSelectionClick}
        menu={
          <IconButton
            aria-label={`Delete tag ${tag.name}`}
            onClick={handleOpenConfirmDialog}
            size="small"
            sx={{
              width: 32,
              height: 32,
              color: "common.white",
              "&:hover": { color: "accent.main" },
            }}
          >
            <DeleteIcon fontSize="small" />
          </IconButton>
        }
      >
        {/* --- Visual Collage Background (now with mixed content) --- */}
        {previewItems.length > 0 ? (
          <Box
            sx={{
              position: "absolute",
              width: "100%",
              height: "100%",
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gridTemplateRows: "1fr 1fr",
              gap: "2px",
            }}
          >
            {/* Map over the unified preview list */}
            {previewItems.map((item, index) => (
              <Box
                key={`${item.type}-${item.id}`}
                component="img"
                src={item.url}
                draggable={false}
                sx={{
                  // Make the first item larger if possible
                  gridRow:
                    index === 0 && previewItems.length > 2 ? "span 2" : "auto",
                  gridColumn:
                    index === 0 && previewItems.length === 2
                      ? "span 2"
                      : "auto",
                  width: "100%",
                  height: "100%",
                  objectFit: "cover",
                  display: "block",
                }}
              />
            ))}
          </Box>
        ) : (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              height: "100%",
              background: (theme) =>
                `linear-gradient(135deg, ${theme.palette.primary.main}, ${theme.palette.primary.dark})`,
            }}
          />
        )}

        {/* --- Gradient Overlay & Content --- */}
        <Box
          sx={{
            position: "absolute",
            top: 0,
            left: 0,
            width: "100%",
            height: "100%",
            background: (theme) =>
              `linear-gradient(to top, ${alpha(theme.palette.common.black, 0.9)} 0%, ${alpha(theme.palette.common.black, 0.1)} 60%, ${alpha(theme.palette.common.black, 0.5)} 100%)`,
            display: "flex",
            flexDirection: "column",
            justifyContent: "flex-end",
            p: 1.5,
            color: (theme) => theme.palette.common.white,
          }}
        >
          <Box>
            <Typography variant="h6" fontWeight="bold" noWrap>
              {tag.name}
            </Typography>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1.5,
                color: (theme) => alpha(theme.palette.common.white, 0.7),
              }}
            >
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                <MovieIcon sx={{ fontSize: "1rem" }} />
                <Typography variant="caption">{tag.media.length}</Typography>
              </Box>
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                <PersonIcon sx={{ fontSize: "1rem" }} />
                <Typography variant="caption">{tag.persons.length}</Typography>
              </Box>
            </Box>
          </Box>
        </Box>
      </SelectableTileFrame>

      {/* --- Themed Confirmation Dialog --- */}
      <Dialog
        open={openConfirmDialog}
        onClose={handleCloseConfirmDialog}
        slotProps={{
          paper: {
            sx: {
              bgcolor: "background.paper",
              color: "text.primary",
              borderRadius: 2,
            },
          },
        }}
      >
        <DialogTitle sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <WarningAmberIcon sx={{ color: "warning.main" }} />
          Delete Tag?
        </DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ color: "text.secondary" }}>
            Are you sure you want to permanently delete the tag{" "}
            <strong>"{tag.name}"</strong>? This action cannot be undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions sx={{ p: "8px 24px 16px 24px" }}>
          <Button onClick={handleCloseConfirmDialog}>Cancel</Button>
          <Button
            onClick={handleConfirmDelete}
            color="error"
            variant="contained"
            autoFocus
          >
            Confirm Delete
          </Button>
        </DialogActions>
      </Dialog>
      <Snackbar
        open={!!errorMessage}
        autoHideDuration={6000}
        onClose={() => setErrorMessage(null)}
      >
        <Alert severity="error" onClose={() => setErrorMessage(null)}>
          {errorMessage}
        </Alert>
      </Snackbar>
    </>
  );
}
