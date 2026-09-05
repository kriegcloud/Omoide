import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import {
  Alert,
  AppBar,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  IconButton,
  Toolbar,
  Typography,
  useTheme,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import SaveAltIcon from "@mui/icons-material/SaveAlt";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import { useNavigate } from "react-router-dom";
import { API } from "../config";
import { editMedia } from "../services/mediaActions";
import { useListStore } from "../stores/useListStore";
import type { Media, MediaDetail } from "../types";
import { encodeFilePath } from "../urlUtils";
import {
  designStateToOps,
  type FilerobotDesignState,
} from "../utils/editorOps";
import ConfirmDialog from "./ConfirmDialog";

interface LazyEditorProps {
  source: string;
  loadableDesignState?: FilerobotDesignState;
  onModify: (state: FilerobotDesignState) => void;
  theme: Record<string, unknown>;
}

const FilerobotImageEditor = lazy(async () => {
  const editorModule = await import("react-filerobot-image-editor");
  const Editor = editorModule.default;

  function ConfiguredEditor(props: LazyEditorProps) {
    return (
      <Editor
        {...props}
        tabsIds={[
          editorModule.TABS.ADJUST,
          editorModule.TABS.FINETUNE,
          editorModule.TABS.RESIZE,
        ]}
        defaultTabId={editorModule.TABS.ADJUST}
        defaultToolId={editorModule.TOOLS.CROP}
        Crop={{
          presetsItems: [
            { titleKey: "square", descriptionKey: "1:1", ratio: 1 },
            { titleKey: "landscape43", descriptionKey: "4:3", ratio: 4 / 3 },
            { titleKey: "photo32", descriptionKey: "3:2", ratio: 3 / 2 },
            { titleKey: "photo75", descriptionKey: "7:5", ratio: 7 / 5 },
            { titleKey: "photo54", descriptionKey: "5:4", ratio: 5 / 4 },
            { titleKey: "portrait45", descriptionKey: "4:5", ratio: 4 / 5 },
            { titleKey: "portrait34", descriptionKey: "3:4", ratio: 3 / 4 },
            { titleKey: "portrait23", descriptionKey: "2:3", ratio: 2 / 3 },
            { titleKey: "portrait57", descriptionKey: "5:7", ratio: 5 / 7 },
          ],
        }}
        Rotate={{ angle: 90, componentType: "buttons" }}
        useZoomPresetsMenu
        disableSaveIfNoChanges
        onBeforeSave={() => false}
        onSave={() => undefined}
      />
    );
  }

  return { default: ConfiguredEditor };
});

interface ImageEditorDialogProps {
  open: boolean;
  media: Media;
  mediaListKey?: string;
  onClose: () => void;
  onSaved?: (detail: MediaDetail, mode: "copy" | "overwrite") => void;
}

export default function ImageEditorDialog({
  open,
  media,
  mediaListKey,
  onClose,
  onSaved,
}: ImageEditorDialogProps) {
  const navigate = useNavigate();
  const muiTheme = useTheme();
  const addItem = useListStore((state) => state.addItem);
  const updateItem = useListStore((state) => state.updateItem);
  const [designState, setDesignState] = useState<FilerobotDesignState | null>(
    (media.edit_design_state as FilerobotDesignState | null) ?? null
  );
  const [hasChanges, setHasChanges] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmOverwrite, setConfirmOverwrite] = useState(false);

  useEffect(() => {
    if (!open) return;
    setDesignState(
      (media.edit_design_state as FilerobotDesignState | null) ?? null
    );
    setHasChanges(false);
    setError(null);
  }, [open, media.id, media.edit_design_state]);

  const editorTheme = useMemo(
    () => ({
      palette: {
        "bg-primary": muiTheme.palette.background.paper,
        "bg-secondary": muiTheme.palette.background.default,
        "accent-primary": muiTheme.palette.primary.main,
        "accent-primary-active": muiTheme.palette.primary.dark,
        "icons-primary": muiTheme.palette.text.primary,
        "icons-secondary": muiTheme.palette.text.secondary,
        "borders-secondary": muiTheme.palette.divider,
      },
      typography: { fontFamily: muiTheme.typography.fontFamily },
    }),
    [muiTheme]
  );

  const source = `${API}/originals/${encodeFilePath(media.path)}${
    media.cache_version ? `?v=${media.cache_version}` : ""
  }`;

  const save = async (mode: "copy" | "overwrite") => {
    if (!designState) return;
    setSaving(true);
    setError(null);
    try {
      if (!media.width || !media.height) {
        throw new Error("Image dimensions are unavailable. Rescan this item and try again.");
      }
      const ops = designStateToOps(
        designState,
        media.width,
        media.height
      );
      if (ops.length === 0) {
        throw new Error("Make at least one image change before saving.");
      }
      const detail = await editMedia(media.id, {
        ops,
        mode,
        design_state: designState,
      });
      if (mode === "overwrite") {
        detail.media.cache_version = Date.now();
        if (mediaListKey) updateItem(mediaListKey, detail.media);
      } else if (mediaListKey) {
        addItem(mediaListKey, detail.media, "start");
      }
      onSaved?.(detail, mode);
      setConfirmOverwrite(false);
      onClose();
      if (mode === "copy") {
        navigate(`/medium/${detail.media.id}`, {
          state: { media: detail.media, mediaListKey },
        });
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to save image edits");
      setConfirmOverwrite(false);
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <Dialog fullScreen open={open} onClose={saving ? undefined : onClose}>
        <AppBar position="relative" color="default" elevation={0}>
          <Toolbar sx={{ gap: 1 }}>
            <IconButton
              edge="start"
              onClick={onClose}
              disabled={saving}
              aria-label="Close image editor"
            >
              <CloseIcon />
            </IconButton>
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="h6" noWrap>
                Edit image
              </Typography>
              <Typography variant="caption" color="text.secondary" noWrap>
                {media.filename}
              </Typography>
            </Box>
          </Toolbar>
        </AppBar>

        <Box sx={{ flex: 1, minHeight: 0, bgcolor: "background.default" }}>
          <Suspense
            fallback={
              <Box
                sx={{ height: "100%", display: "grid", placeItems: "center" }}
              >
                <CircularProgress aria-label="Loading image editor" />
              </Box>
            }
          >
            <FilerobotImageEditor
              source={source}
              loadableDesignState={designState ?? undefined}
              onModify={(nextState) => {
                setDesignState(nextState);
                setHasChanges(true);
                setError(null);
              }}
              theme={editorTheme}
            />
          </Suspense>
        </Box>

        {error && (
          <Alert severity="error" square>
            {error}
          </Alert>
        )}
        <DialogActions
          sx={{ px: { xs: 2, sm: 3 }, py: 1.5, borderTop: 1, borderColor: "divider" }}
        >
          <Button onClick={onClose} disabled={saving} sx={{ mr: "auto" }}>
            Cancel
          </Button>
          <Button
            color="warning"
            variant="outlined"
            startIcon={<WarningAmberIcon />}
            onClick={() => setConfirmOverwrite(true)}
            disabled={!hasChanges || saving}
          >
            Overwrite original
          </Button>
          <Button
            variant="contained"
            startIcon={saving ? <CircularProgress size={16} /> : <SaveAltIcon />}
            onClick={() => void save("copy")}
            disabled={!hasChanges || saving}
          >
            Save copy
          </Button>
        </DialogActions>
      </Dialog>

      <ConfirmDialog
        open={confirmOverwrite}
        title="Overwrite the original image?"
        message="The original file will be replaced. Faces will be detected again after saving; manual person links will be kept. This cannot be undone."
        confirmLabel="Overwrite original"
        confirmColor="warning"
        loading={saving}
        onClose={() => setConfirmOverwrite(false)}
        onConfirm={() => void save("overwrite")}
      />
    </>
  );
}
