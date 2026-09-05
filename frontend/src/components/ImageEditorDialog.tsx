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
  Divider,
  FormControlLabel,
  IconButton,
  List,
  ListItem,
  ListItemText,
  Popover,
  Stack,
  Switch,
  Toolbar,
  Typography,
  useTheme,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import CompareIcon from "@mui/icons-material/Compare";
import GridOnIcon from "@mui/icons-material/GridOn";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";
import SaveAltIcon from "@mui/icons-material/SaveAlt";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import { useNavigate } from "react-router-dom";
import { API } from "../config";
import { editMedia, getFaceCropSuggestions } from "../services/mediaActions";
import { useListStore } from "../stores/useListStore";
import { useLastEditStore } from "../stores/useLastEditStore";
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

interface ImageOverlayLayout {
  left: number;
  top: number;
  width: number;
  height: number;
  rotation: number;
}

const ASPECT_BUCKETS = [512, 768, 1024] as const;

const SHORTCUTS = [
  ["R / Shift+R", "Rotate right / left"],
  ["H / V", "Flip horizontally / vertically"],
  ["C", "Select the crop tool"],
  ["0", "Zoom to fit"],
  ["` (hold)", "Compare with original"],
  ["Ctrl/⌘+S", "Save copy"],
  ["Ctrl/⌘+Shift+S", "Overwrite original"],
  ["Esc", "Cancel"],
] as const;

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
  const setLastEdit = useLastEditStore((state) => state.setLastEdit);
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
  const [aspectGuides, setAspectGuides] = useState(false);
  const [compareHeld, setCompareHeld] = useState(false);
  const [comparePinned, setComparePinned] = useState(false);
  const [shortcutsAnchor, setShortcutsAnchor] = useState<HTMLElement | null>(null);
  const [faceSuggestions, setFaceSuggestions] = useState<FaceCropSuggestion[] | null>(null);
  const [imageOverlay, setImageOverlay] = useState<ImageOverlayLayout | null>(null);

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
    setAspectGuides(false);
    setCompareHeld(false);
    setComparePinned(false);
    setShortcutsAnchor(null);
    setFaceSuggestions(null);
    setImageOverlay(null);
  }, [open, media.id, initialDesignState]);

  const updateImageOverlay = async () => {
    const live = await captureStoreState();
    const shown = live?.shownImageDimensions;
    const host = editorHostRef.current;
    const canvas = host?.querySelector<HTMLElement>(".FIE_canvas-container");
    if (!shown?.width || !shown.height || !host || !canvas) {
      setImageOverlay(null);
      return;
    }
    const hostRect = host.getBoundingClientRect();
    const canvasRect = canvas.getBoundingClientRect();
    setImageOverlay({
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
      await updateImageOverlay();
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
      window.setTimeout(() => { void updateImageOverlay(); }, 0);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to apply face framing");
    }
  };

  useEffect(() => {
    if (!faceGuides && !aspectGuides && !compareHeld && !comparePinned) return;
    const onResize = () => { void updateImageOverlay(); };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [faceGuides, aspectGuides, compareHeld, comparePinned]);

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

  const updateAdjustments = (
    transform: (
      adjustments: NonNullable<FilerobotDesignState["adjustments"]>
    ) => NonNullable<FilerobotDesignState["adjustments"]>
  ) => {
    const updateState = updateStateRef.current;
    if (!updateState) return;
    updateState((live) => ({
      adjustments: transform(live.adjustments ?? {}),
    }));
    setHasChanges(true);
    setError(null);
    window.setTimeout(() => { void updateImageOverlay(); }, 0);
  };

  const selectCropTool = () => {
    // Filerobot 4.9.1 exposes no public select-tool API. Keep this fallback
    // scoped to its documented tools bar class so a missing selector is safe.
    editorHostRef.current
      ?.querySelector<HTMLElement>(".FIE_tools-bar .FIE_crop-tool")
      ?.click();
  };

  const zoomToFit = () => {
    const host = editorHostRef.current;
    if (!host) return;
    let zoomLabel = host.querySelector<HTMLElement>(".FIE_topbar-zoom-label");
    if (zoomLabel?.getAttribute("aria-disabled") === "true") {
      // Filerobot disables zoom while Crop is selected. Selecting a passive
      // finetune tool enables its own zoom menu without changing image data.
      host.querySelector<HTMLElement>(".FIE_brightness-tool-button")?.click();
      zoomLabel = host.querySelector<HTMLElement>(".FIE_topbar-zoom-label");
    }
    zoomLabel?.click();
    window.setTimeout(() => {
      // The zoom menu is portalled outside the editor host. Filerobot has no
      // public zoom API, so invoke its translated Fit-size preset by label.
      const menu = document.querySelector<HTMLElement>(".FIE_topbar-zoom-menu");
      const fitLabel = Array.from(menu?.querySelectorAll<HTMLElement>("*") ?? [])
        .find((element) => element.children.length === 0 && element.textContent?.trim() === "Fit size");
      (fitLabel?.closest<HTMLElement>("[role='menuitem'], button, li") ?? fitLabel?.parentElement)?.click();
      window.setTimeout(() => { void updateImageOverlay(); }, 0);
    }, 0);
  };

  const toggleCompare = (pinned: boolean) => {
    setComparePinned(pinned);
    if (pinned) void updateImageOverlay();
  };

  const toggleAspectGuides = (visible: boolean) => {
    setAspectGuides(visible);
    if (visible) void updateImageOverlay();
  };

  const save = async (saveMode: "copy" | "overwrite") => {
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
        mode: saveMode,
        design_state: freshState,
      });
      setLastEdit(ops);
      if (saveMode === "overwrite") {
        detail.media.cache_version = Date.now();
        if (mediaListKey) updateItem(mediaListKey, detail.media);
      } else if (mediaListKey) {
        addItem(mediaListKey, detail.media, "start");
      }
      onSaved?.(detail, saveMode);
      setConfirmOverwrite(false);
      onClose();
      if (saveMode === "copy") {
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

  useEffect(() => {
    if (!open) return;

    const isEditableTarget = (target: EventTarget | null) =>
      target instanceof HTMLElement &&
      (target.matches("input, textarea, select") ||
        target.isContentEditable ||
        Boolean(target.closest("[contenteditable='true']")));

    const onKeyDown = (event: KeyboardEvent) => {
      if (isEditableTarget(event.target)) return;

      if (event.code === "Backquote" && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        if (!event.repeat) {
          setCompareHeld(true);
          void updateImageOverlay();
        }
        return;
      }

      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (!hasChanges || saving) return;
        if (event.shiftKey && mode === "write") setConfirmOverwrite(true);
        else if (!event.shiftKey) void save("copy");
        return;
      }

      if (event.ctrlKey || event.metaKey || event.altKey) return;
      switch (event.key.toLowerCase()) {
        case "escape":
          event.preventDefault();
          if (saving) return;
          if (confirmOverwrite) setConfirmOverwrite(false);
          else if (shortcutsAnchor) setShortcutsAnchor(null);
          else onClose();
          break;
        case "r":
          event.preventDefault();
          updateAdjustments((adjustments) => ({
            ...adjustments,
            rotation: (adjustments.rotation ?? 0) + (event.shiftKey ? -90 : 90),
          }));
          break;
        case "h":
          event.preventDefault();
          updateAdjustments((adjustments) => ({
            ...adjustments,
            isFlippedX: !adjustments.isFlippedX,
          }));
          break;
        case "v":
          event.preventDefault();
          updateAdjustments((adjustments) => ({
            ...adjustments,
            isFlippedY: !adjustments.isFlippedY,
          }));
          break;
        case "c":
          event.preventDefault();
          selectCropTool();
          break;
        case "0":
          event.preventDefault();
          zoomToFit();
          break;
      }
    };

    const stopComparing = (event?: KeyboardEvent) => {
      if (!event || event.code === "Backquote") setCompareHeld(false);
    };

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", stopComparing);
    window.addEventListener("blur", stopComparing);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", stopComparing);
      window.removeEventListener("blur", stopComparing);
    };
  }, [
    open,
    hasChanges,
    saving,
    mode,
    confirmOverwrite,
    shortcutsAnchor,
    onClose,
    designState,
  ]);

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
              <Button
                size="small"
                startIcon={<CompareIcon />}
                variant={comparePinned ? "contained" : "text"}
                aria-pressed={comparePinned}
                onClick={() => toggleCompare(!comparePinned)}
                sx={{ whiteSpace: "nowrap" }}
              >
                Compare
              </Button>
              <Button
                size="small"
                startIcon={<GridOnIcon />}
                variant={aspectGuides ? "contained" : "text"}
                aria-pressed={aspectGuides}
                onClick={() => toggleAspectGuides(!aspectGuides)}
                sx={{ whiteSpace: "nowrap" }}
              >
                Bucket guides
              </Button>
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
              <IconButton
                size="small"
                aria-label="Show editor keyboard shortcuts"
                aria-haspopup="true"
                aria-expanded={Boolean(shortcutsAnchor)}
                onClick={(event) => setShortcutsAnchor(event.currentTarget)}
              >
                <HelpOutlineIcon fontSize="small" />
              </IconButton>
            </Stack>
          </Toolbar>
        </AppBar>
        <Popover
          open={Boolean(shortcutsAnchor)}
          anchorEl={shortcutsAnchor}
          onClose={() => setShortcutsAnchor(null)}
          anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
          transformOrigin={{ vertical: "top", horizontal: "right" }}
        >
          <Box sx={{ minWidth: 280, p: 1 }}>
            <Typography variant="subtitle2" sx={{ px: 1, py: 0.75 }}>
              Keyboard shortcuts
            </Typography>
            <Divider />
            <List dense disablePadding sx={{ pt: 0.5 }}>
              {SHORTCUTS.map(([keys, action]) => (
                <ListItem key={keys} sx={{ py: 0.25 }}>
                  <ListItemText
                    primary={action}
                    secondary={keys}
                    primaryTypographyProps={{ variant: "body2" }}
                    secondaryTypographyProps={{ variant: "caption" }}
                  />
                </ListItem>
              ))}
            </List>
          </Box>
        </Popover>

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
                if (faceGuides || aspectGuides || compareHeld || comparePinned) {
                  window.setTimeout(() => { void updateImageOverlay(); }, 0);
                }
              }}
              theme={editorTheme}
            />
            )}
          </Suspense>
          {faceGuides && imageOverlay && media.width && media.height && ((imageOverlay.rotation % 360) + 360) % 360 === 0 && (
            <Box sx={{ position: "absolute", pointerEvents: "none", zIndex: 5, left: imageOverlay.left, top: imageOverlay.top, width: imageOverlay.width, height: imageOverlay.height, overflow: "hidden" }}>
              {(faceSuggestions ?? []).map((suggestion) => {
                const [x, y, width, height] = suggestion.face_bbox;
                return (
                  <Box key={suggestion.face_id} sx={{ position: "absolute", border: "2px solid", borderColor: "warning.light", boxShadow: "0 0 0 1px rgba(0,0,0,0.6)", left: `${(x / media.width) * 100}%`, top: `${(y / media.height) * 100}%`, width: `${(width / media.width) * 100}%`, height: `${(height / media.height) * 100}%` }} />
                );
              })}
            </Box>
          )}
          {aspectGuides && imageOverlay && media.width && media.height && ((imageOverlay.rotation % 360) + 360) % 360 === 0 && (
            <Box sx={{ position: "absolute", pointerEvents: "none", zIndex: 5, left: imageOverlay.left, top: imageOverlay.top, width: imageOverlay.width, height: imageOverlay.height, overflow: "hidden" }}>
              {ASPECT_BUCKETS.filter((bucket) => bucket <= Math.min(media.width!, media.height!)).map((bucket) => {
                const width = bucket * imageOverlay.width / media.width!;
                const height = bucket * imageOverlay.height / media.height!;
                return (
                  <Box
                    key={bucket}
                    sx={{
                      position: "absolute",
                      left: `calc(50% - ${width / 2}px)`,
                      top: `calc(50% - ${height / 2}px)`,
                      width,
                      height,
                      border: "1px solid",
                      borderColor: "info.light",
                      boxShadow: "0 0 0 1px rgba(0,0,0,0.45)",
                    }}
                  >
                    <Typography
                      component="span"
                      variant="caption"
                      sx={{
                        position: "absolute",
                        top: 2,
                        left: 4,
                        px: 0.5,
                        color: "common.white",
                        bgcolor: "rgba(0,0,0,0.68)",
                        borderRadius: 0.5,
                      }}
                    >
                      {bucket}
                    </Typography>
                  </Box>
                );
              })}
            </Box>
          )}
          {(compareHeld || comparePinned) && imageOverlay && (
            <Box
              component="img"
              src={source}
              alt="Original image preview"
              sx={{
                position: "absolute",
                pointerEvents: "none",
                zIndex: 6,
                left: imageOverlay.left,
                top: imageOverlay.top,
                width: imageOverlay.width,
                height: imageOverlay.height,
                objectFit: "contain",
                bgcolor: "background.default",
              }}
            />
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
