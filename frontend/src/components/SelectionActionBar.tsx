import React, { useState } from "react";
import {
  Button,
  Chip,
  Fade,
  Paper,
  Snackbar,
  Alert,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import ReplayIcon from "@mui/icons-material/Replay";
import PhotoAlbumIcon from "@mui/icons-material/PhotoAlbum";
import PersonAddIcon from "@mui/icons-material/PersonAdd";
import PersonRemoveIcon from "@mui/icons-material/PersonRemove";
import DriveFileMoveIcon from "@mui/icons-material/DriveFileMove";
import { matchPath, useLocation } from "react-router-dom";
import { useSelection } from "../context/SelectionContext";
import { RerunProcessorsDialog } from "./RerunProcessorsDialog";
import { AddToAlbumDialog } from "./AddToAlbumDialog";
import AssignMediaToPersonDialog from "./AssignMediaToPersonDialog";
import FolderPickerDialog from "./FolderPickerDialog";
import { bulkMoveMedia } from "../services/mediaActions";
import { detachMediaFromPersonBulk } from "../services/personActions";
import { useListStore } from "../stores/useListStore";

export const SelectionActionBar: React.FC = () => {
  const { selectedIds, clear } = useSelection();
  const { removeItems } = useListStore();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [albumDialogOpen, setAlbumDialogOpen] = useState(false);
  const [assignDialogOpen, setAssignDialogOpen] = useState(false);
  const [folderDialogOpen, setFolderDialogOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const location = useLocation();
  const personMatch = matchPath({ path: "/person/:id/*", end: false }, location.pathname)
    ?? matchPath("/person/:id", location.pathname);
  const personId = personMatch?.params.id ? Number(personMatch.params.id) : null;
  const [snackbar, setSnackbar] = useState<{
    open: boolean;
    message: string;
    severity: "success" | "error";
  }>({ open: false, message: "", severity: "success" });

  const count = selectedIds.size;

  return (
    <>
      <Fade in={count > 0}>
        <Paper
          elevation={4}
          sx={{
            position: "fixed",
            bottom: 24,
            left: "50%",
            transform: "translateX(-50%)",
            zIndex: 1250,
            px: 2,
            py: 1,
            display: "flex",
            alignItems: "center",
            flexWrap: "wrap",
            justifyContent: "center",
            gap: 1.5,
            borderRadius: 6,
            bgcolor: "background.paper",
            maxWidth: "calc(100vw - 32px)",
          }}
        >
          <Chip label={`${count} selected`} size="small" color="primary" />
          <Button
            size="small"
            startIcon={<PhotoAlbumIcon fontSize="small" />}
            onClick={() => setAlbumDialogOpen(true)}
            variant="contained"
            disableElevation
          >
            Add to Album
          </Button>
          <Button
            size="small"
            startIcon={<ReplayIcon fontSize="small" />}
            onClick={() => setDialogOpen(true)}
            variant="contained"
            disableElevation
          >
            Rerun Processors
          </Button>
          <Button
            size="small"
            startIcon={<PersonAddIcon fontSize="small" />}
            onClick={() => setAssignDialogOpen(true)}
            variant="contained"
            disableElevation
          >
            Attach to person
          </Button>
          <Button
            size="small"
            startIcon={<DriveFileMoveIcon fontSize="small" />}
            onClick={() => setFolderDialogOpen(true)}
            variant="contained"
            disableElevation
          >
            Move to folder
          </Button>
          {personId !== null && Number.isFinite(personId) && (
            <Button
              size="small"
              startIcon={<PersonRemoveIcon fontSize="small" />}
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  const result = await detachMediaFromPersonBulk(personId, Array.from(selectedIds));
                  setSnackbar({
                    open: true,
                    message: `Detached ${result.detached_ids?.length ?? 0} item(s).`,
                    severity: "success",
                  });
                  for (const key of Object.keys(useListStore.getState().lists)) {
                    if (key.startsWith(`person-${personId}-media-appearances-`)) {
                      removeItems(key, result.detached_ids ?? []);
                    }
                  }
                  clear();
                } catch (error) {
                  setSnackbar({
                    open: true,
                    message: error instanceof Error ? error.message : "Failed to detach media",
                    severity: "error",
                  });
                } finally {
                  setBusy(false);
                }
              }}
              color="warning"
            >
              Detach from this person
            </Button>
          )}
          <Button
            size="small"
            startIcon={<CloseIcon fontSize="small" />}
            onClick={clear}
            color="inherit"
          >
            Clear
          </Button>
        </Paper>
      </Fade>

      <RerunProcessorsDialog
        open={dialogOpen}
        mediaIds={Array.from(selectedIds)}
        onClose={() => setDialogOpen(false)}
        onStarted={() =>
          setSnackbar({ open: true, message: "Processing started.", severity: "success" })
        }
      />

      <AddToAlbumDialog
        open={albumDialogOpen}
        mediaIds={Array.from(selectedIds)}
        onClose={() => setAlbumDialogOpen(false)}
        onAdded={(album) => {
          setSnackbar({
            open: true,
            message: `Added to "${album.name}".`,
            severity: "success",
          });
          clear();
        }}
      />

      <AssignMediaToPersonDialog
        open={assignDialogOpen}
        mediaIds={Array.from(selectedIds)}
        onClose={() => setAssignDialogOpen(false)}
        onAssigned={(person, skippedCount) => {
          setSnackbar({
            open: true,
            message: `Attached to ${person.name ?? "person"}${skippedCount ? `; ${skippedCount} skipped` : ""}.`,
            severity: "success",
          });
          clear();
        }}
      />

      <FolderPickerDialog
        open={folderDialogOpen}
        loading={busy}
        onClose={() => setFolderDialogOpen(false)}
        onConfirm={async (destination) => {
          setBusy(true);
          try {
            const result = await bulkMoveMedia(Array.from(selectedIds), destination);
            setSnackbar({
              open: true,
              message: `Moved ${result.moved_ids.length} item(s)${result.skipped.length ? `; ${result.skipped.length} skipped` : ""}.`,
              severity: "success",
            });
            setFolderDialogOpen(false);
            clear();
          } catch (error) {
            setSnackbar({
              open: true,
              message: error instanceof Error ? error.message : "Failed to move media",
              severity: "error",
            });
          } finally {
            setBusy(false);
          }
        }}
      />

      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={() => setSnackbar((s) => ({ ...s, open: false }))}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert severity={snackbar.severity} variant="filled" sx={{ width: "100%" }}>
          {snackbar.message}
        </Alert>
      </Snackbar>
    </>
  );
};
