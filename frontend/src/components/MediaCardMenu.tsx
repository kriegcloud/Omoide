import { optimisticOperations, projectEntity } from "../stores/optimisticOperations";
import { useEditorUrlState } from "../hooks/useEditorUrlState";
import { useLocation, useNavigate } from "react-router-dom";
import { MouseEvent, useEffect, useRef, useState, useSyncExternalStore } from "react";
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
import LinkOffIcon from "@mui/icons-material/LinkOff";
import DatasetIcon from "@mui/icons-material/Dataset";
import DeleteIcon from "@mui/icons-material/Delete";
import DeleteForeverIcon from "@mui/icons-material/DeleteForever";
import BuildIcon from "@mui/icons-material/Build";
import config from "../config";
import { Media, MediaPreview } from "../types";
import {
  deleteMediaFile,
  deleteMediaRecord,
  moveMedia,
  openMediaFile,
  renameMedia,
  setMediaFavorite,
} from "../services/mediaActions";
import { useUndoRefresh } from "../context/UndoContext";
import { refreshCachedList } from "../stores/useListStore";
import AssignMediaToPersonDialog from "./AssignMediaToPersonDialog";
import ConfirmDialog from "./ConfirmDialog";
import FolderPickerDialog from "./FolderPickerDialog";
import RenameMediaDialog from "./RenameMediaDialog";
import AddToDatasetDialog from "./AddToDatasetDialog";
import RepairDialog from "./RepairDialog";
import { startRepair } from "../services/repairs";
import type { RepairProfile } from "../types";
import { useTileContextMenu, type TileMenuPoint } from "./tileContextMenu";
import { editorOpenTarget } from "../utils/editorUrlState";


export interface MediaPersonContext {
  personId: number;
  // Detach this item from the person (undoable through the page's handler).
  onUnmatch?: () => void | Promise<void>;
  unmatchLabel?: string;
}

interface MediaCardMenuProps {
  media: MediaPreview | Media;
  mediaListKey?: string;
  personContext?: MediaPersonContext;
  onMediaChange?: (media: Media | MediaPreview) => void;
  onDeleted?: () => void;
}

type DialogKind = "edit" | "rename" | "move" | "assign" | "dataset" | "backgroundSwap" | "upscale" | "deleteRecord" | "deleteFile" | null;

export default function MediaCardMenu({
  media,
  mediaListKey,
  personContext,
  onMediaChange,
  onDeleted,
}: MediaCardMenuProps) {
  useUndoRefresh(mediaListKey ? `cache:${mediaListKey}` : undefined, () => refreshCachedList(mediaListKey!));
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null);
  const [anchorPoint, setAnchorPoint] = useState<TileMenuPoint | null>(null);
  useTileContextMenu((point) => {
    setAnchorEl(null);
    setAnchorPoint(point);
  });
  const [repairAnchorEl, setRepairAnchorEl] = useState<HTMLElement | null>(null);
  const [dialog, setDialog] = useState<DialogKind>(null);
  const [busy, setBusy] = useState(false);
  const deleteBusyRef = useRef(false);
  const [deleteTarget, setDeleteTarget] = useState<{
    id: number;
    mediaListKey?: string;
    onDeleted?: () => void;
  } | null>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);
  useSyncExternalStore(optimisticOperations.subscribe, optimisticOperations.getSnapshot, optimisticOperations.getSnapshot);
  const favorite = !!projectEntity(`media:${media.id}`, media)?.is_favorite;
  const favoriteChange = useRef({ media, onMediaChange });
  favoriteChange.current = { media, onMediaChange };
  useEffect(() => {
    const { media: currentMedia, onMediaChange: changed } = favoriteChange.current;
    changed?.({ ...currentMedia, is_favorite: favorite });
  }, [favorite, media.id]);
  const { open: openEditorUrl } = useEditorUrlState();
  const location = useLocation();
  const navigate = useNavigate();
  const [snackbar, setSnackbar] = useState<{ message: string; severity: "success" | "error" } | null>(null);


  const stop = (event: MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
  };
  const closeMenu = () => {
    setAnchorEl(null);
    setAnchorPoint(null);
  };
  const openDialog = (kind: DialogKind) => {
    closeMenu();
    setDialogError(null);
    if (kind === "deleteRecord" || kind === "deleteFile") {
      setDeleteTarget({ id: media.id, mediaListKey, onDeleted });
    }
    setDialog(kind);
  };
  const fail = (error: unknown, fallback: string) => {
    const message = error instanceof Error ? error.message : fallback;
    setDialogError(message);
    setSnackbar({ message, severity: "error" });
  };

  const toggleFavorite = () => {
    closeMenu();
    void setMediaFavorite(media.id, !favorite).catch(() => {});
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

  const openEditor = () => {
    closeMenu();
    const target = editorOpenTarget(location.pathname, media.id);
    if (target.kind === "navigate") {
      navigate(target.to, { state: { ...location.state, editorListKey: mediaListKey } });
      return;
    }
    openEditorUrl({ edit: target.edit }, { ...location.state, editorListKey: mediaListKey });
  };

  const confirmRename = (filename: string) => {
    setDialog(null);
    void renameMedia(media.id, filename).catch(() => {});
  };

  const confirmMove = (destinationDir: string) => {
    setDialog(null);
    void moveMedia(media.id, destinationDir).catch(() => {});
  };

  const closeDeleteDialog = () => {
    if (deleteBusyRef.current) return;
    setDialog(null);
    setDeleteTarget(null);
  };
  const confirmDelete = (deleteFile: boolean) => {
    if (!deleteTarget || deleteBusyRef.current || config.PRESENTATION_MODE) return;
    const target = deleteTarget;
    deleteBusyRef.current = true;
    const request = deleteFile ? deleteMediaFile(target.id) : deleteMediaRecord(target.id);
    setDialog(null);
    setDeleteTarget(null);
    // Retained for legacy parents that do not consume the shared list overlay.
    void request.then(() => target.onDeleted?.()).catch(() => {}).finally(() => { deleteBusyRef.current = false; });
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
            setAnchorPoint(null);
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
        anchorReference={anchorPoint ? "anchorPosition" : "anchorEl"}
        anchorPosition={anchorPoint ?? undefined}
        anchorEl={anchorEl}
        open={Boolean(anchorEl) || Boolean(anchorPoint)}
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
        <MenuItem onClick={() => openDialog("assign")}>
          <ListItemIcon><PersonAddIcon /></ListItemIcon>
          Assign to person…
        </MenuItem>
        {personContext?.onUnmatch && (
          <MenuItem onClick={() => { closeMenu(); void personContext.onUnmatch?.(); }}>
            <ListItemIcon><LinkOffIcon /></ListItemIcon>
            {personContext.unmatchLabel ?? "Unmatch from person"}
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
        <MenuItem disabled={busy} onClick={() => { setRepairAnchorEl(null); closeMenu(); setDialog("upscale"); }}>Upscale</MenuItem>
        {personContext && <MenuItem disabled={busy} onClick={() => void beginRepair("omoide-remove-people-v1")}>Remove other people</MenuItem>}
        {personContext && <MenuItem disabled={busy} onClick={() => { setRepairAnchorEl(null); closeMenu(); setDialog("backgroundSwap"); }}>Swap background…</MenuItem>}
      </Menu>

      <RepairDialog
        open={dialog === "backgroundSwap" || dialog === "upscale"}
        mediaIds={[media.id]}
        personId={personContext?.personId}
        initialProfile={dialog === "upscale" ? "omoide-upscale-v1" : "omoide-background-swap-v1"}
        onClose={() => setDialog(null)}
        onStarted={() => setSnackbar({ message: dialog === "upscale" ? "Upscale started" : "Background swap started", severity: "success" })}
      />

      <RenameMediaDialog
        open={dialog === "rename"}
        filename={media.filename}
        loading={busy}
        error={dialogError}
        onClose={() => setDialog(null)}
        onConfirm={(filename) => void confirmRename(filename)}
      />
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
          if (personContext) {
            onDeleted?.();
          }
          setSnackbar({ message: `Assigned to ${person.name ?? "person"}`, severity: "success" });
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
        onClose={closeDeleteDialog}
        onConfirm={() => void confirmDelete(false)}
      />
      <ConfirmDialog
        open={dialog === "deleteFile"}
        title="Delete File from Disk?"
        message="The file will be permanently deleted from disk. This cannot be undone."
        confirmLabel="Delete File"
        loading={busy}
        onClose={closeDeleteDialog}
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
