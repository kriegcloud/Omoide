import { MouseEvent, useEffect, useState } from "react";
import {
  Alert,
  Divider,
  IconButton,
  ListItemIcon,
  Menu,
  MenuItem,
  Snackbar,
  Tooltip,
} from "@mui/material";
import MoreVertIcon from "@mui/icons-material/MoreVert";
import FavoriteIcon from "@mui/icons-material/Favorite";
import FavoriteBorderIcon from "@mui/icons-material/FavoriteBorder";
import EditIcon from "@mui/icons-material/Edit";
import DriveFileRenameOutlineIcon from "@mui/icons-material/DriveFileRenameOutline";
import DriveFileMoveIcon from "@mui/icons-material/DriveFileMove";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import PersonAddIcon from "@mui/icons-material/PersonAdd";
import DatasetIcon from "@mui/icons-material/Dataset";
import DeleteIcon from "@mui/icons-material/Delete";
import DeleteForeverIcon from "@mui/icons-material/DeleteForever";
import BuildIcon from "@mui/icons-material/Build";
import config from "../config";
import { Media, MediaPreview } from "../types";
import { getMedia } from "../services/media";
import {
  deleteMediaFile,
  deleteMediaRecord,
  moveMedia,
  openMediaFile,
  renameMedia,
  setMediaFavorite,
} from "../services/mediaActions";
import { useListStore } from "../stores/useListStore";
import AssignMediaToPersonDialog from "./AssignMediaToPersonDialog";
import ConfirmDialog from "./ConfirmDialog";
import FolderPickerDialog from "./FolderPickerDialog";
import RenameMediaDialog from "./RenameMediaDialog";
import ImageEditorDialog from "./ImageEditorDialog";
import AddToDatasetDialog from "./AddToDatasetDialog";
import RepairDialog from "./RepairDialog";
import { startRepair } from "../services/repairs";
import type { RepairProfile } from "../types";

export interface MediaPersonContext {
  personId: number;
}

interface MediaCardMenuProps {
  media: MediaPreview | Media;
  mediaListKey?: string;
  personContext?: MediaPersonContext;
  onMediaChange?: (media: Media | MediaPreview) => void;
  onDeleted?: () => void;
}

type DialogKind = "edit" | "rename" | "move" | "assign" | "dataset" | "backgroundSwap" | "deleteRecord" | "deleteFile" | null;

export default function MediaCardMenu({
  media,
  mediaListKey,
  personContext,
  onMediaChange,
  onDeleted,
}: MediaCardMenuProps) {
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null);
  const [repairAnchorEl, setRepairAnchorEl] = useState<HTMLElement | null>(null);
  const [dialog, setDialog] = useState<DialogKind>(null);
  const [busy, setBusy] = useState(false);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [favorite, setFavorite] = useState(!!media.is_favorite);
  const [editorMedia, setEditorMedia] = useState<Media | null>(null);
  const [snackbar, setSnackbar] = useState<{ message: string; severity: "success" | "error" } | null>(null);
  const { updateItem, removeItem } = useListStore();

  useEffect(() => setFavorite(!!media.is_favorite), [media.id, media.is_favorite]);

  const stop = (event: MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
  };
  const closeMenu = () => setAnchorEl(null);
  const openDialog = (kind: DialogKind) => {
    closeMenu();
    setDialogError(null);
    setDialog(kind);
  };
  const applyMedia = (updated: Media | MediaPreview) => {
    setFavorite(updated.is_favorite);
    if (mediaListKey) updateItem(mediaListKey, updated);
    onMediaChange?.(updated);
  };
  const fail = (error: unknown, fallback: string) => {
    const message = error instanceof Error ? error.message : fallback;
    setDialogError(message);
    setSnackbar({ message, severity: "error" });
  };

  const toggleFavorite = async () => {
    closeMenu();
    const next = !favorite;
    setFavorite(next);
    try {
      applyMedia(await setMediaFavorite(media.id, next));
    } catch (error) {
      setFavorite(!next);
      fail(error, "Failed to update favorite");
    }
  };

  const beginRepair = async (profile: RepairProfile) => {
    setRepairAnchorEl(null);
    closeMenu();
    setBusy(true);
    try {
      await startRepair(media.id, profile, { personId: personContext?.personId });
      setSnackbar({ message: "Repair started", severity: "success" });
    } catch (error) {
      fail(error, "Failed to start repair");
    } finally {
      setBusy(false);
    }
  };

  const openEditor = async () => {
    closeMenu();
    setBusy(true);
    try {
      const detail = await getMedia(String(media.id));
      setEditorMedia(detail.media);
      setDialog("edit");
    } catch (error) {
      fail(error, "Failed to load image editor");
    } finally {
      setBusy(false);
    }
  };

  const confirmRename = async (filename: string) => {
    setBusy(true);
    setDialogError(null);
    try {
      applyMedia(await renameMedia(media.id, filename));
      setDialog(null);
      setSnackbar({ message: "Media renamed", severity: "success" });
    } catch (error) {
      fail(error, "Failed to rename media");
    } finally {
      setBusy(false);
    }
  };

  const confirmMove = async (destinationDir: string) => {
    setBusy(true);
    setDialogError(null);
    try {
      applyMedia(await moveMedia(media.id, destinationDir));
      setDialog(null);
      setSnackbar({ message: "Media moved", severity: "success" });
    } catch (error) {
      fail(error, "Failed to move media");
    } finally {
      setBusy(false);
    }
  };

  const confirmDelete = async (deleteFile: boolean) => {
    setBusy(true);
    try {
      if (deleteFile) await deleteMediaFile(media.id);
      else await deleteMediaRecord(media.id);
      if (mediaListKey) removeItem(mediaListKey, media.id);
      setDialog(null);
      onDeleted?.();
      setSnackbar({
        message: deleteFile ? "File deleted" : "Record removed",
        severity: "success",
      });
    } catch (error) {
      fail(error, deleteFile ? "Failed to delete file" : "Failed to remove record");
    } finally {
      setBusy(false);
    }
  };

  const openApplicationItem = (
    <MenuItem
      disabled={config.IS_DOCKER}
      onClick={() => {
        closeMenu();
        void openMediaFile(media.id).catch((error) => fail(error, "Failed to open media"));
      }}
    >
      <ListItemIcon><OpenInNewIcon /></ListItemIcon>
      Open in default application
    </MenuItem>
  );

  return (
    <>
      <Tooltip title="Media actions">
        <IconButton
          aria-label="Media actions"
          size="small"
          onClick={(event) => {
            stop(event);
            setAnchorEl(event.currentTarget);
          }}
          sx={{
            color: "common.white",
            bgcolor: "rgba(0,0,0,0.48)",
            "&:hover": { bgcolor: "rgba(0,0,0,0.68)" },
          }}
        >
          <MoreVertIcon fontSize="small" />
        </IconButton>
      </Tooltip>
      <Menu
        anchorEl={anchorEl}
        open={Boolean(anchorEl)}
        onClose={closeMenu}
        onClick={(event) => event.stopPropagation()}
      >
        <MenuItem onClick={() => void toggleFavorite()}>
          <ListItemIcon>{favorite ? <FavoriteIcon color="error" /> : <FavoriteBorderIcon />}</ListItemIcon>
          {favorite ? "Unfavorite" : "Favorite"}
        </MenuItem>
        {typeof media.duration === "number" ? (
          <Tooltip title="Image editing is not available for videos" placement="left">
            <span>
              <MenuItem disabled>
                <ListItemIcon><EditIcon /></ListItemIcon>
                Edit…
              </MenuItem>
            </span>
          </Tooltip>
        ) : (
          <MenuItem onClick={() => void openEditor()}>
            <ListItemIcon><EditIcon /></ListItemIcon>
            Edit…
          </MenuItem>
        )}
        <Tooltip
          title={config.REPAIRS_ENABLED ? "Choose an image repair" : "Image repairs are disabled"}
          placement="left"
        >
          <span>
            <MenuItem
              disabled={!config.REPAIRS_ENABLED || typeof media.duration === "number"}
              onClick={(event) => setRepairAnchorEl(event.currentTarget)}
            >
              <ListItemIcon><BuildIcon /></ListItemIcon>
              Repair ▸
            </MenuItem>
          </span>
        </Tooltip>
        <MenuItem onClick={() => openDialog("rename")}>
          <ListItemIcon><DriveFileRenameOutlineIcon /></ListItemIcon>
          Rename…
        </MenuItem>
        <MenuItem onClick={() => openDialog("move")}>
          <ListItemIcon><DriveFileMoveIcon /></ListItemIcon>
          Move to folder…
        </MenuItem>
        {config.IS_DOCKER ? (
          <Tooltip title="Not available in the Docker deployment" placement="left">
            <span>{openApplicationItem}</span>
          </Tooltip>
        ) : openApplicationItem}
        {personContext && (
          <MenuItem onClick={() => openDialog("assign")}>
            <ListItemIcon><PersonAddIcon /></ListItemIcon>
            Assign to person…
          </MenuItem>
        )}
        <MenuItem onClick={() => openDialog("dataset")}>
          <ListItemIcon><DatasetIcon /></ListItemIcon>
          Add to dataset…
        </MenuItem>
        <Divider />
        <MenuItem onClick={() => openDialog("deleteRecord")}>
          <ListItemIcon><DeleteIcon color="error" /></ListItemIcon>
          Delete record…
        </MenuItem>
        <MenuItem onClick={() => openDialog("deleteFile")}>
          <ListItemIcon><DeleteForeverIcon color="error" /></ListItemIcon>
          Delete file…
        </MenuItem>
      </Menu>
      <Menu
        anchorEl={repairAnchorEl}
        open={Boolean(repairAnchorEl)}
        onClose={() => setRepairAnchorEl(null)}
        anchorOrigin={{ vertical: "top", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
      >
        <MenuItem disabled={busy} onClick={() => void beginRepair("omoide-remove-text-v1")}>Remove overlays</MenuItem>
        <MenuItem disabled={busy} onClick={() => void beginRepair("omoide-upscale-v1")}>Upscale</MenuItem>
        {personContext && <MenuItem disabled={busy} onClick={() => void beginRepair("omoide-remove-people-v1")}>Remove other people</MenuItem>}
        {personContext && <MenuItem disabled={busy} onClick={() => { setRepairAnchorEl(null); closeMenu(); setDialog("backgroundSwap"); }}>Swap background…</MenuItem>}
      </Menu>

      <RepairDialog
        open={dialog === "backgroundSwap"}
        mediaIds={[media.id]}
        personId={personContext?.personId}
        initialProfile="omoide-background-swap-v1"
        onClose={() => setDialog(null)}
        onStarted={() => setSnackbar({ message: "Background swap started", severity: "success" })}
      />

      <RenameMediaDialog
        open={dialog === "rename"}
        filename={media.filename}
        loading={busy}
        error={dialogError}
        onClose={() => setDialog(null)}
        onConfirm={(filename) => void confirmRename(filename)}
      />
      {editorMedia && (
        <ImageEditorDialog
          open={dialog === "edit"}
          media={editorMedia}
          mediaListKey={mediaListKey}
          onClose={() => setDialog(null)}
          onSaved={(detail, mode) => {
            if (mode === "overwrite") applyMedia(detail.media);
            setSnackbar({
              message: mode === "overwrite" ? "Original image updated" : "Edited copy saved",
              severity: "success",
            });
          }}
        />
      )}
      <FolderPickerDialog
        open={dialog === "move"}
        loading={busy}
        onClose={() => setDialog(null)}
        onConfirm={(destination) => void confirmMove(destination)}
      />
      <AssignMediaToPersonDialog
        open={dialog === "assign"}
        mediaIds={[media.id]}
        sourcePersonId={personContext?.personId}
        onClose={() => setDialog(null)}
        onAssigned={(person) => {
          if (personContext && mediaListKey) removeItem(mediaListKey, media.id);
          setSnackbar({ message: `Assigned to ${person.name ?? "person"}`, severity: "success" });
          onDeleted?.();
        }}
      />
      <AddToDatasetDialog
        open={dialog === "dataset"}
        mediaIds={[media.id]}
        onClose={() => setDialog(null)}
        onAdded={(dataset) => setSnackbar({ message: `Added to ${dataset.name}`, severity: "success" })}
      />
      <ConfirmDialog
        open={dialog === "deleteRecord"}
        title="Remove from Library?"
        message="The record will be removed from the database. The file on disk is kept and can be re-imported by scanning."
        confirmLabel="Remove Record"
        loading={busy}
        onClose={() => setDialog(null)}
        onConfirm={() => void confirmDelete(false)}
      />
      <ConfirmDialog
        open={dialog === "deleteFile"}
        title="Delete File from Disk?"
        message="The file will be permanently deleted from disk. This cannot be undone."
        confirmLabel="Delete File"
        loading={busy}
        onClose={() => setDialog(null)}
        onConfirm={() => void confirmDelete(true)}
      />
      <Snackbar
        open={Boolean(snackbar)}
        autoHideDuration={3500}
        onClose={() => setSnackbar(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert severity={snackbar?.severity ?? "success"} variant="filled" onClose={() => setSnackbar(null)}>
          {snackbar?.message}
        </Alert>
      </Snackbar>
    </>
  );
}
