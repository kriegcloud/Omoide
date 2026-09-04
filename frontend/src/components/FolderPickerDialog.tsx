import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Breadcrumbs,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Link,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  TextField,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import FolderIcon from "@mui/icons-material/Folder";
import { getMediaFolders } from "../services/media";
import { createMediaFolder } from "../services/mediaActions";
import { MediaFolderListing } from "../types";

interface FolderPickerDialogProps {
  open: boolean;
  loading?: boolean;
  onClose: () => void;
  onConfirm: (destinationDir: string) => void;
}

export default function FolderPickerDialog({
  open,
  loading = false,
  onClose,
  onConfirm,
}: FolderPickerDialogProps) {
  const [currentPath, setCurrentPath] = useState("");
  const [listing, setListing] = useState<MediaFolderListing | null>(null);
  const [fetching, setFetching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newFolderName, setNewFolderName] = useState("");
  const [creating, setCreating] = useState(false);

  const load = (path: string) => {
    setFetching(true);
    setError(null);
    getMediaFolders(path || null, 0, true)
      .then((data) => {
        setListing(data);
        setCurrentPath(data.current_path ?? "");
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load folders"))
      .finally(() => setFetching(false));
  };

  useEffect(() => {
    if (!open) return;
    setCurrentPath("");
    setNewFolderName("");
    load("");
  }, [open]);

  const createFolder = async () => {
    const name = newFolderName.trim();
    if (!name) return;
    setCreating(true);
    setError(null);
    try {
      const folder = await createMediaFolder(currentPath, name);
      setNewFolderName("");
      load(folder.path);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create folder");
    } finally {
      setCreating(false);
    }
  };

  const busy = loading || creating;
  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} fullWidth maxWidth="sm">
      <DialogTitle>Move to folder</DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <Breadcrumbs sx={{ mb: 2 }}>
          <Link component="button" underline="hover" onClick={() => load("")}>
            Media
          </Link>
          {listing?.breadcrumbs.map((crumb) => (
            <Link
              component="button"
              underline="hover"
              key={crumb.path ?? "root"}
              onClick={() => load(crumb.path ?? "")}
            >
              {crumb.name}
            </Link>
          ))}
        </Breadcrumbs>
        <Box sx={{ display: "flex", gap: 1, mb: 2 }}>
          <TextField
            size="small"
            fullWidth
            label="New folder"
            value={newFolderName}
            disabled={busy}
            onChange={(event) => setNewFolderName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void createFolder();
            }}
          />
          <Button
            variant="outlined"
            startIcon={<AddIcon />}
            disabled={busy || !newFolderName.trim()}
            onClick={() => void createFolder()}
          >
            Create
          </Button>
        </Box>
        {fetching ? (
          <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
            <CircularProgress size={28} />
          </Box>
        ) : listing?.folders.length ? (
          <List disablePadding>
            {listing.folders.map((folder) => (
              <ListItemButton key={folder.path} onClick={() => load(folder.path)}>
                <ListItemIcon><FolderIcon color="primary" /></ListItemIcon>
                <ListItemText
                  primary={folder.name}
                  secondary={`${folder.media_count} media item${folder.media_count === 1 ? "" : "s"}`}
                />
              </ListItemButton>
            ))}
          </List>
        ) : (
          <Typography color="text.secondary" sx={{ py: 3, textAlign: "center" }}>
            This folder has no subfolders.
          </Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button
          variant="contained"
          disabled={busy || fetching}
          onClick={() => onConfirm(currentPath)}
        >
          {loading ? "Moving…" : "Move here"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
