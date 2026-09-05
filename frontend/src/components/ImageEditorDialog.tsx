import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import type { getCurrentImgDataFunction } from "react-filerobot-image-editor";
import {
  Alert,
  AppBar,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  FormControlLabel,
  IconButton,
  Stack,
  Switch,
  Toolbar,
  Typography,
  useTheme,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import SaveAltIcon from "@mui/icons-material/SaveAlt";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import { useNavigate } from "react-router-dom";
import { API } from "../config";
import { editMedia, getFaceCropSuggestions } from "../services/mediaActions";
import { useListStore } from "../stores/useListStore";
import type { CropFraming, FaceCropSuggestion, Media, MediaDetail } from "../types";
import { encodeFilePath } from "../urlUtils";
import {
  designStateToOps,
  type EditOp,
  type FilerobotDesignState,
} from "../utils/editorOps";
import ConfirmDialog from "./ConfirmDialog";

interface LazyEditorProps {
  source: string;
  loadableDesignState?: FilerobotDesignState;
  onModify: (state: FilerobotDesignState) => void;
  getCurrentImgDataFnRef: { current?: getCurrentImgDataFunction };
  updateStateFnRef: { current?: FilerobotUpdateState };
  theme: Record<string, unknown>;
}

/**
 * Filerobot's UPDATE_STATE reducer accepts a function payload and calls it
 * with the live store state. Returning null leaves the state untouched, which
 * turns the setter into a reader for the current crop box, rotation, flips,
 * shown-image dimensions and the Konva design layer.
 */
type FilerobotUpdateState = (
  newStatePart:
    | Record<string, unknown>
    | ((currentState: FilerobotStoreState) => Record<string, unknown> | null)
) => void;

interface FilerobotStoreState {
  adjustments?: FilerobotDesignState["adjustments"];
  resize?: FilerobotDesignState["resize"];
  finetunesProps?: FilerobotDesignState["finetunesProps"];
  shownImageDimensions?: FilerobotDesignState["shownImageDimensions"];
  designLayer?: {
    attrs?: { clipX?: number; clipY?: number; clipWidth?: number; clipHeight?: number };
  };
}

interface FaceOverlayLayout {
  left: number;
  top: number;
  width: number;
  height: number;
  rotation: number;
}

const FRAMING_PRESETS: Array<{ framing: CropFraming; label: string }> = [
  { framing: "closeup", label: "Close-up" },
  { framing: "portrait", label: "Portrait" },
  { framing: "half_body", label: "Half body" },
  { framing: "full_body", label: "Full body" },
];

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
        observePluginContainerSize
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
  mode?: "write" | "virtual";
  loadableDesignState?: FilerobotDesignState | null;
  onOpsReady?: (ops: EditOp[], designState: FilerobotDesignState) => void;
}

export default function ImageEditorDialog({
  open,
  media,
  mediaListKey,
  onClose,
  onSaved,
  mode = "write",
  loadableDesignState,
  onOpsReady,
}: ImageEditorDialogProps) {
  const navigate = useNavigate();
  const muiTheme = useTheme();
  const addItem = useListStore((state) => state.addItem);
  const updateItem = useListStore((state) => state.updateItem);
  // Filerobot treats loadableDesignState as a state to (re)load, so it must
  // only ever carry the saved state the dialog opened with. Feeding the live
  // onModify state back into it re-applies every change and recurses until
  // the stack overflows.
  const initialDesignState = useMemo(
    () => loadableDesignState ?? (media.edit_design_state as FilerobotDesignState | null) ?? undefined,
    [open, media.id, loadableDesignState]
  );
  const [designState, setDesignState] = useState<FilerobotDesignState | null>(
    initialDesignState ?? null
  );
  const [hasChanges, setHasChanges] = useState(false);
  // Filerobot fills this with a getter for the *current* design state. The
  // onModify callback can lag one interaction behind (e.g. a crop preset
  // click updates the ratio before the crop box), so saves read from here.
  const currentImgDataRef = useRef<getCurrentImgDataFunction | undefined>(
    undefined
  );
  const updateStateRef = useRef<FilerobotUpdateState | undefined>(undefined);
  const editorHostRef = useRef<HTMLDivElement>(null);
  const [faceGuides, setFaceGuides] = useState(false);
  const [faceSuggestions, setFaceSuggestions] = useState<FaceCropSuggestion[] | null>(null);
  const [faceOverlay, setFaceOverlay] = useState<FaceOverlayLayout | null>(null);

  const captureStoreState = async (): Promise<FilerobotStoreState | null> => {
    const updateState = updateStateRef.current;
    if (!updateState) return null;
    let captured: FilerobotStoreState | null = null;
    updateState((state) => {
      captured = state;
      return null;
    });
    for (let attempt = 0; attempt < 5 && !captured; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 16));
    }
    return captured;
  };

  const readLiveDesignState = async (): Promise<FilerobotDesignState | null> => {
    const live = await captureStoreState();
    if (!live) return null;
    const clip = live.designLayer?.attrs;
    const crop = live.adjustments?.crop;
    return {
      ...(designState ?? {}),
      adjustments: {
        ...live.adjustments,
        crop: crop
          ? {
              ...crop,
              // Filerobot's own save falls back to the canvas clip box when
              // the state has no explicit values (useTransformedImgData).
              x: crop.x ?? clip?.clipX,
              y: crop.y ?? clip?.clipY,
              width: crop.width ?? clip?.clipWidth,
              height: crop.height ?? clip?.clipHeight,
            }
          : crop,
      },
      resize: live.resize,
      finetunesProps: live.finetunesProps,
      shownImageDimensions: live.shownImageDimensions,
    };
  };
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmOverwrite, setConfirmOverwrite] = useState(false);
  // Filerobot measures its canvas container once on mount and derives the
  // design layer + shown-image dimensions from it. Inside a fading full-screen
  // dialog that container has no size yet, so mount the editor only after the
  // transition finished (and let Filerobot observe later resizes).
  const [entered, setEntered] = useState(false);

  useEffect(() => {
    if (!open) return;
    setDesignState(initialDesignState ?? null);
    setEntered(false);
    setHasChanges(false);
    setError(null);
    setFaceGuides(false);
    setFaceSuggestions(null);
    setFaceOverlay(null);
  }, [open, media.id, initialDesignState]);

  const updateFaceOverlay = async () => {
    const live = await captureStoreState();
    const shown = live?.shownImageDimensions;
    const host = editorHostRef.current;
    const canvas = host?.querySelector<HTMLElement>(".FIE_canvas-container");
    if (!shown?.width || !shown.height || !host || !canvas) {
      setFaceOverlay(null);
      return;
    }
    const hostRect = host.getBoundingClientRect();
    const canvasRect = canvas.getBoundingClientRect();
    setFaceOverlay({
      left: canvasRect.left - hostRect.left + (shown.x ?? 0),
      top: canvasRect.top - hostRect.top + (shown.y ?? 0),
      width: shown.width,
      height: shown.height,
      rotation: live?.adjustments?.rotation ?? 0,
    });
  };

  const toggleFaceGuides = async (checked: boolean) => {
    setFaceGuides(checked);
    if (!checked) return;
    try {
      if (faceSuggestions === null) {
        setFaceSuggestions(await getFaceCropSuggestions(media.id));
      }
      await updateFaceOverlay();
    } catch (reason) {
      setFaceGuides(false);
      setError(reason instanceof Error ? reason.message : "Failed to load face guides");
    }
  };

  const applyFramingPreset = async (framing: CropFraming) => {
    setError(null);
    try {
      if (!media.width || !media.height) {
        throw new Error("Image dimensions are unavailable. Rescan this item and try again.");
      }
      const suggestions = await getFaceCropSuggestions(media.id, framing, "free");
      const suggestion = suggestions[0];
      if (!suggestion) throw new Error("No detected face is available for this preset.");
      const live = await captureStoreState();
      const shown = live?.shownImageDimensions;
      if (!shown?.width || !shown.height || !updateStateRef.current) {
        throw new Error("The editor image is still loading. Try the preset again.");
      }
      const crop = suggestion.crop_op;
      updateStateRef.current({
        adjustments: {
          crop: {
            x: crop.x * shown.width / media.width,
            y: crop.y * shown.height / media.height,
            width: crop.width * shown.width / media.width,
            height: crop.height * shown.height / media.height,
            ratio: "custom",
          },
        },
      });
      setHasChanges(true);
      window.setTimeout(() => { void updateFaceOverlay(); }, 0);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to apply face framing");
    }
  };

  useEffect(() => {
    if (!faceGuides) return;
    const onResize = () => { void updateFaceOverlay(); };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [faceGuides]);

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
    setSaving(true);
    setError(null);
    try {
      if (!media.width || !media.height) {
        throw new Error("Image dimensions are unavailable. Rescan this item and try again.");
      }
      let current: ReturnType<getCurrentImgDataFunction> | undefined;
      try {
        current = currentImgDataRef.current?.({}, false, false);
      } catch (reason) {
        // Known to throw in some embeddings when the design layer is not yet
        // registered in Filerobot's store; the live-state read below covers it.
        if (import.meta.env.DEV) console.debug("Filerobot getCurrentImgData unavailable", reason);
      }
      const liveState = await readLiveDesignState();
      const freshState =
        liveState ??
        (current?.designState
          ? (current.designState as unknown as FilerobotDesignState)
          : designState);
      if (!freshState) {
        throw new Error("The editor state could not be read. Close the editor and try again.");
      }
      const ops = designStateToOps(
        freshState,
        media.width,
        media.height,
        current?.imageData
          ? { width: current.imageData.width, height: current.imageData.height }
          : undefined
      );
      if (import.meta.env.DEV) {
        console.debug(
          "image edit ops",
          JSON.stringify({
            ops,
            imageData: current?.imageData
              ? { width: current.imageData.width, height: current.imageData.height }
              : null,
            adjustments: freshState.adjustments,
            shown: freshState.shownImageDimensions,
            resize: freshState.resize,
            source: { width: media.width, height: media.height },
          })
        );
      }
      if (ops.length === 0) {
        throw new Error("Make at least one image change before saving.");
      }
      if (mode === "virtual") {
        onOpsReady?.(ops, freshState);
        onClose();
        return;
      }
      const detail = await editMedia(media.id, {
        ops,
        mode,
        design_state: freshState,
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
      <Dialog
        fullScreen
        open={open}
        onClose={saving ? undefined : onClose}
        TransitionProps={{
          onEntered: () => setEntered(true),
          onExited: () => setEntered(false),
        }}
      >
        <AppBar position="relative" color="default" elevation={0}>
          <Toolbar sx={{ gap: 1, flexWrap: "wrap" }}>
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
            <Stack direction="row" spacing={0.5} alignItems="center" sx={{ ml: { sm: "auto" }, overflowX: "auto" }}>
              <FormControlLabel
                control={<Switch size="small" checked={faceGuides} onChange={(event) => void toggleFaceGuides(event.target.checked)} />}
                label="Face guides"
                sx={{ whiteSpace: "nowrap", mr: 0.5 }}
              />
              {FRAMING_PRESETS.map((preset) => (
                <Button key={preset.framing} size="small" variant="outlined" onClick={() => void applyFramingPreset(preset.framing)} sx={{ whiteSpace: "nowrap" }}>
                  {preset.label}
                </Button>
              ))}
            </Stack>
          </Toolbar>
        </AppBar>

        <Box ref={editorHostRef} sx={{ flex: 1, minHeight: 0, bgcolor: "background.default", position: "relative" }}>
          <Suspense
            fallback={
              <Box
                sx={{ height: "100%", display: "grid", placeItems: "center" }}
              >
                <CircularProgress aria-label="Loading image editor" />
              </Box>
            }
          >
            {entered && (
            <FilerobotImageEditor
              source={source}
              loadableDesignState={initialDesignState}
              getCurrentImgDataFnRef={currentImgDataRef}
              updateStateFnRef={updateStateRef}
              onModify={(nextState) => {
                setDesignState(nextState);
                setHasChanges(true);
                setError(null);
                if (faceGuides) window.setTimeout(() => { void updateFaceOverlay(); }, 0);
              }}
              theme={editorTheme}
            />
            )}
          </Suspense>
          {faceGuides && faceOverlay && media.width && media.height && ((faceOverlay.rotation % 360) + 360) % 360 === 0 && (
            <Box sx={{ position: "absolute", pointerEvents: "none", zIndex: 5, left: faceOverlay.left, top: faceOverlay.top, width: faceOverlay.width, height: faceOverlay.height, overflow: "hidden" }}>
              {(faceSuggestions ?? []).map((suggestion) => {
                const [x, y, width, height] = suggestion.face_bbox;
                return (
                  <Box key={suggestion.face_id} sx={{ position: "absolute", border: "2px solid", borderColor: "warning.light", boxShadow: "0 0 0 1px rgba(0,0,0,0.6)", left: `${(x / media.width) * 100}%`, top: `${(y / media.height) * 100}%`, width: `${(width / media.width) * 100}%`, height: `${(height / media.height) * 100}%` }} />
                );
              })}
            </Box>
          )}
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
          {mode === "write" && (
            <Button
              color="warning"
              variant="outlined"
              startIcon={<WarningAmberIcon />}
              onClick={() => setConfirmOverwrite(true)}
              disabled={!hasChanges || saving}
            >
              Overwrite original
            </Button>
          )}
          <Button
            variant="contained"
            startIcon={saving ? <CircularProgress size={16} /> : <SaveAltIcon />}
            onClick={() => void save("copy")}
            disabled={!hasChanges || saving}
          >
            {mode === "virtual" ? "Use crop" : "Save copy"}
          </Button>
        </DialogActions>
      </Dialog>

      {mode === "write" && <ConfirmDialog
        open={confirmOverwrite}
        title="Overwrite the original image?"
        message="The original file will be replaced. Faces will be detected again after saving; manual person links will be kept. This cannot be undone."
        confirmLabel="Overwrite original"
        confirmColor="warning"
        loading={saving}
        onClose={() => setConfirmOverwrite(false)}
        onConfirm={() => void save("overwrite")}
      />}
    </>
  );
}
