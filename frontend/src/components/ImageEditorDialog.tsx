import { EDITOR_A11Y_ATTR, observeEditorAccessibility } from "../utils/editorA11y";
import { useEditorUrlState } from "../hooks/useEditorUrlState";
import { measureEditorImage, sameOverlay, type EditorStoreLike, type ImageOverlayLayout } from "../utils/editorOverlay";
import { editorSelection, readEditorOptions } from "../utils/editorUrlState";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { useDialogHotkeyScope, useHotkey, useHotkeyHelp, useHotkeys } from "../hotkeys/useHotkey";
import { editMedia, getFaceCropSuggestions } from "../services/mediaActions";
import { useListStore } from "../stores/useListStore";
import { useLastEditStore } from "../stores/useLastEditStore";
import type { CropFraming, FaceCropSuggestion, Media, MediaDetail } from "../types";
import { encodeFilePath } from "../urlUtils";
import {
  designStateToOps,
  editorCanvasOutput,
  type EditOp,
  type FilerobotDesignState,
} from "../utils/editorOps";
import EditorCropControls from "./EditorCropControls";
import { buildEditorControlStyles, buildEditorTheme } from "../utils/editorTheme";
import EditorUpscalePanel from "./EditorUpscalePanel";
import ConfirmDialog from "./ConfirmDialog";
import FaceBoxOverlay from "./FaceBoxOverlay";
import { observeEditorFits } from "../utils/editorFitObserver";

interface LazyEditorProps {
  source: string;
  loadableDesignState?: FilerobotDesignState;
  onModify: (state: FilerobotDesignState) => void;
  getCurrentImgDataFnRef: { current?: getCurrentImgDataFunction };
  updateStateFnRef: { current?: FilerobotUpdateState };
  theme: Record<string, unknown>;
  defaultTabId: "Adjust" | "Finetune" | "Resize";
  defaultToolId: string;
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
  tabId?: string;
  toolId?: string;
  omoideFitRevision?: number;
  pastDesignStates?: FilerobotDesignState[];
  futureDesignStates?: FilerobotDesignState[];
  adjustments?: FilerobotDesignState["adjustments"];
  resize?: FilerobotDesignState["resize"];
  finetunesProps?: FilerobotDesignState["finetunesProps"];
  shownImageDimensions?: FilerobotDesignState["shownImageDimensions"];
  designLayer?: {
    attrs?: { clipX?: number; clipY?: number; clipWidth?: number; clipHeight?: number };
  };
}


const ASPECT_BUCKETS = [512, 768, 1024] as const;

const SHORTCUTS = [
  ["R / Shift+R", "Rotate right / left"],
  ["H / V", "Flip horizontally / vertically"],
  ["C", "Select the crop tool"],
  ["0", "Zoom to fit"],
  ["U", "Upscale image"],
  ["` (hold)", "Compare with original"],
  ["Ctrl/⌘+S", "Save copy"],
  ["Ctrl/⌘+Shift+S", "Overwrite original"],
  ["Esc", "Cancel"],
] as const;

const EDITOR_HOTKEYS = [
  { key: "r", description: "Rotate image right" },
  { key: "r", shift: true, description: "Rotate image left" },
  { key: "h", description: "Flip image horizontally" },
  { key: "v", description: "Flip image vertically" },
  { key: "c", description: "Select crop tool" },
  { key: "0", description: "Zoom image to fit" },
  { key: "`", description: "Hold to compare with original" },
  { key: "~", shift: true, description: "Hold to compare with original" },
  { key: "Escape", description: "Close image editor" },
] as const;

const OVERWRITE_HOTKEYS = [
  { key: "s", ctrl: true, shift: true, description: "Confirm overwrite original" },
  { key: "s", meta: true, shift: true, description: "Confirm overwrite original" },
] as const;

const EDITOR_SAVING_PIXEL_RATIO = 1;

const FilerobotImageEditor = lazy(async () => {
  const editorModule = await import("react-filerobot-image-editor");
  const Editor = editorModule.default;

  function ConfiguredEditor(props: LazyEditorProps) {
    return (
      <Editor
        {...props}
        loadableDesignState={props.loadableDesignState as React.ComponentProps<typeof Editor>["loadableDesignState"]}
        savingPixelRatio={EDITOR_SAVING_PIXEL_RATIO}
        previewPixelRatio={1}
        tabsIds={[
          editorModule.TABS.ADJUST,
          editorModule.TABS.FINETUNE,
          editorModule.TABS.RESIZE,
        ]}
        defaultTabId={props.defaultTabId}
        defaultToolId={props.defaultToolId as React.ComponentProps<typeof Editor>["defaultToolId"]}
        Crop={{ noPresets: true }}
        Rotate={{ angle: 90, componentType: "buttons" }}
        useZoomPresetsMenu
        observePluginContainerSize
        removeSaveButton
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
  onOpsReady?: (ops: EditOp[], designState: FilerobotDesignState) => void | Promise<void>;
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
  const { params: editorParams, patch: patchEditor } = useEditorUrlState();
  const urlOptions = readEditorOptions(editorParams);
  const initialTool = useRef(urlOptions);
  const appliedPreset = useRef<CropFraming | null>(null);
  const setGuides = (bucket: boolean, face: boolean) => patchEditor({ guides: [bucket && "bucket", face && "face"].filter(Boolean).join(",") || null });
  const muiTheme = useTheme();
  const hotkeyScope = useDialogHotkeyScope(open);
  const addItem = useListStore((state) => state.addItem);
  const updateItem = useListStore((state) => state.updateItem);
  const setLastEdit = useLastEditStore((state) => state.setLastEdit);
  // Media edit_design_state is historical: copy/overwrite already baked it
  // into the source pixels. Only virtual edits supply unapplied operations
  // for their unchanged source; every write session starts neutral.
  // Filerobot treats loadableDesignState as a state to (re)load, so it must
  // only ever carry the saved state the dialog opened with. Feeding the live
  // onModify state back into it re-applies every change and recurses until
  // the stack overflows.
  const initialDesignState = useMemo(
    () => mode === "virtual" ? loadableDesignState ?? undefined : undefined,
    [mode, loadableDesignState]
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

  const captureStoreState = useCallback(async (): Promise<FilerobotStoreState | null> => {
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
  }, []);

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
    const host = editorHostRef.current;
    if (!open || !entered || !host) return;
    return observeEditorAccessibility(host);
  }, [open, entered]);

  // Filerobot does not notify onModify for automatic fits. Observe its live
  // store while mounted and keep an unchanged crop in the same
  // source space. The read returns null (a reducer bailout); idle frames do not
  // render. Apply only to the snapshot read, so an intervening edit wins.
  // Stop the observer with the dialog/editor lifecycle.
  useEffect(() => {
    if (!open || !entered) return;
    return observeEditorFits(
      async () => {
        const state = await captureStoreState();
        return state ? {
          adjustments: state.adjustments, shownImageDimensions: state.shownImageDimensions,
          pastDesignStates: state.pastDesignStates, futureDesignStates: state.futureDesignStates,
          omoideFitRevision: state.omoideFitRevision,
        } : null;
      },
      update => updateStateRef.current?.(current => update({adjustments: current.adjustments, shownImageDimensions: current.shownImageDimensions, pastDesignStates: current.pastDesignStates, futureDesignStates: current.futureDesignStates, omoideFitRevision: current.omoideFitRevision}) ?? null),
      callback => window.requestAnimationFrame(callback),
      frame => window.cancelAnimationFrame(frame),
    );
  }, [open, entered, media.id, captureStoreState]);

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

  const updateImageOverlay = useCallback(async () => {
    const live = await captureStoreState();
    const host = editorHostRef.current;
    const canvas = host?.querySelector<HTMLElement>(".FIE_canvas-container");
    const stage = canvas?.querySelector<HTMLElement>(".konvajs-content") ?? canvas;
    if (!host) {
      setImageOverlay(null);
      return;
    }
    const next = measureEditorImage(
      live as unknown as EditorStoreLike | null,
      host.getBoundingClientRect(),
      stage?.getBoundingClientRect() ?? null,
    );
    setImageOverlay((previous) => (sameOverlay(previous, next) ? previous : next));
  }, [captureStoreState]);

  // filerobot zooms/pans (wheel, +/- buttons, drag, pinch, tool/tab changes)
  // without calling onModify; re-measure after any such interaction.
  const overlayActive = faceGuides || aspectGuides;
  useEffect(() => {
    if (!open || !overlayActive) return;
    const host = editorHostRef.current;
    if (!host) return;
    let frame = 0;
    const timers = new Set<number>();
    const measure = () => { void updateImageOverlay(); };
    const schedule = () => {
      if (!frame) frame = window.requestAnimationFrame(() => { frame = 0; measure(); });
      const timer = window.setTimeout(() => { timers.delete(timer); measure(); }, 150);
      timers.add(timer);
    };
    const events = ["wheel", "pointerup", "click", "keyup", "touchend"] as const;
    events.forEach((name) => host.addEventListener(name, schedule, { passive: true }));
    // The canvas sizes itself after mount (and on tab/tool panel changes):
    // observe it so the first layout is measured without an interaction.
    const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(schedule);
    const observeCanvas = () => {
      const canvas = host.querySelector<HTMLElement>(".FIE_canvas-container");
      const stage = canvas?.querySelector<HTMLElement>(".konvajs-content");
      if (canvas) resizeObserver?.observe(canvas);
      if (stage) resizeObserver?.observe(stage);
    };
    observeCanvas();
    const mutationObserver = typeof MutationObserver === "undefined" ? null : new MutationObserver(() => { observeCanvas(); schedule(); });
    mutationObserver?.observe(host, { childList: true, subtree: true });
    [400, 1000].forEach((delay) => {
      const timer = window.setTimeout(() => { timers.delete(timer); measure(); }, delay);
      timers.add(timer);
    });
    schedule();
    return () => {
      resizeObserver?.disconnect();
      mutationObserver?.disconnect();
      events.forEach((name) => host.removeEventListener(name, schedule));
      if (frame) window.cancelAnimationFrame(frame);
      timers.forEach((timer) => window.clearTimeout(timer));
    };
  }, [open, overlayActive, updateImageOverlay]);

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

  const applyFramingPreset = async (framing: CropFraming, persist = true) => {
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
      appliedPreset.current = framing;
      if (persist) patchEditor({ preset: framing });
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
  }, [faceGuides, aspectGuides, compareHeld, comparePinned, updateImageOverlay]);

  const cropImage = useMemo(() => ({ width: media.width ?? 1, height: media.height ?? 1 }), [media.width, media.height]);
  const editorTheme = useMemo(() => buildEditorTheme(muiTheme), [muiTheme]);

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

  // The public ref exposes the same store fields used by SELECT_TAB/SELECT_TOOL.
  // Wait for the actual image dimensions before restoring guides or a face crop.
  const restoreActions = useRef({ toggleFaceGuides, toggleAspectGuides, toggleCompare, applyFramingPreset });
  restoreActions.current = { toggleFaceGuides, toggleAspectGuides, toggleCompare, applyFramingPreset };
  const { tabId, toolId, face, bucket, compare, preset } = urlOptions;
  useEffect(() => {
    if (!open || !entered) return;
    let cancelled = false;
    let timer: number;
    const restore = async () => {
      const live = await captureStoreState();
      if (cancelled) return;
      if (!live?.shownImageDimensions?.width || !updateStateRef.current) {
        timer = window.setTimeout(() => { void restore(); }, 80);
        return;
      }
      updateStateRef.current({ tabId, toolId, selectionsIds: [] });
      const actions = restoreActions.current;
      actions.toggleCompare(compare);
      actions.toggleAspectGuides(bucket);
      await actions.toggleFaceGuides(face);
      if (cancelled) return;
      if (preset && appliedPreset.current !== preset) await actions.applyFramingPreset(preset, false);
      if (!preset) appliedPreset.current = null;
    };
    void restore();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [open, entered, tabId, toolId, face, bucket, compare, preset, captureStoreState]);

  const captureToolSelection = () => {
    window.setTimeout(() => {
      void captureStoreState().then(live => {
        if (!live?.tabId) return;
        const tool = editorSelection(live.tabId, live.toolId ?? null);
        if (tool !== editorSelection(tabId, toolId)) patchEditor({ tool });
      });
    }, 0);
  };

  const save = async (saveMode: "copy" | "overwrite", keepOpen = false): Promise<MediaDetail | null> => {
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
        editorCanvasOutput(current?.imageData, EDITOR_SAVING_PIXEL_RATIO)
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
        await onOpsReady?.(ops, freshState);
        onClose();
        return null;
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
      if (!keepOpen) onSaved?.(detail, saveMode);
      setConfirmOverwrite(false);
      if (!keepOpen) onClose();
      if (saveMode === "copy" && !keepOpen) {
        navigate(`/medium/${detail.media.id}`, {
          state: { media: detail.media, mediaListKey },
        });
      }
      return detail;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to save image edits");
      setConfirmOverwrite(false);
      return null;
    } finally {
      setSaving(false);
    }
  };

  const onEditorKeyDown = (event: KeyboardEvent) => {
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

  useHotkey({ key: "u" }, () => patchEditor({ tool: "upscale" }), { scope: "dialog", dialogRef: hotkeyScope, enabled: open && mode === "write" && !saving, description: "Upscale image" });
  const openHelp = useHotkeyHelp();
  useHotkey({ key: "?" }, openHelp, { scope: "dialog", dialogRef: hotkeyScope, enabled: open, description: "Show keyboard shortcuts" });
  useHotkeys(EDITOR_HOTKEYS, onEditorKeyDown, {
    scope: "dialog",
    enabled: open,
    dialogRef: hotkeyScope,
  });
  useHotkeys([
    { key: "s", ctrl: true, description: mode === "virtual" ? "Save virtual image edits" : "Save image copy" },
    { key: "s", meta: true, description: mode === "virtual" ? "Save virtual image edits" : "Save image copy" },
  ], onEditorKeyDown, {
    scope: "dialog",
    enabled: open,
    dialogRef: hotkeyScope,
  });
  useHotkeys(OVERWRITE_HOTKEYS, onEditorKeyDown, {
    scope: "dialog",
    enabled: open && mode === "write",
    destructive: true,
    dialogRef: hotkeyScope,
  });

  useEffect(() => {
    if (!open) return;
    const stopComparing = (event?: KeyboardEvent | FocusEvent) => {
      if (!event || event.type === "blur" || ("code" in event && event.code === "Backquote")) setCompareHeld(false);
    };

    window.addEventListener("keyup", stopComparing);
    window.addEventListener("blur", stopComparing);
    return () => {
      window.removeEventListener("keyup", stopComparing);
      window.removeEventListener("blur", stopComparing);
    };
  }, [open]);

  return (
    <>
      <Dialog
        ref={hotkeyScope}
        fullScreen
        open={open}
        aria-labelledby="image-editor-title image-editor-filename"
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
              <Typography variant="h6" component="h2" id="image-editor-title" noWrap>
                Edit image
              </Typography>
              <Typography variant="caption" color="text.secondary" id="image-editor-filename" noWrap>
                {media.filename}
              </Typography>
            </Box>
            <Stack direction="row" spacing={0.5} alignItems="center" sx={{ ml: { sm: "auto" }, overflowX: "auto" }}>
              {mode === "write" && <Button size="small" disabled={saving} onClick={() => patchEditor({ tool: "upscale" })} sx={{ whiteSpace: "nowrap" }}>Upscale…</Button>}
              <Button
                size="small"
                startIcon={<CompareIcon />}
                variant={comparePinned ? "contained" : "text"}
                aria-pressed={comparePinned}
                onClick={() => { toggleCompare(!comparePinned); patchEditor({ compare: !comparePinned ? "1" : null }); }}
                sx={{ whiteSpace: "nowrap" }}
              >
                Compare
              </Button>
              <Button
                size="small"
                startIcon={<GridOnIcon />}
                variant={aspectGuides ? "contained" : "text"}
                aria-pressed={aspectGuides}
                onClick={() => { toggleAspectGuides(!aspectGuides); setGuides(!aspectGuides, faceGuides); }}
                sx={{ whiteSpace: "nowrap" }}
              >
                Bucket guides
              </Button>
              <FormControlLabel
                control={<Switch size="small" checked={faceGuides} onChange={(event) => { void toggleFaceGuides(event.target.checked); setGuides(aspectGuides, event.target.checked); }} />}
                label="Face guides"
                sx={{ whiteSpace: "nowrap", mr: 0.5 }}
              />
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
        {mode === "write" && <EditorUpscalePanel
          open={urlOptions.upscale}
          media={media}
          hasChanges={hasChanges}
          initialProvider={urlOptions.upscaler}
          onProviderChange={provider => patchEditor({ upscaler: provider })}
          saveCopy={async () => (await save("copy", true))?.media ?? null}
          onClose={() => patchEditor({ tool: editorSelection(tabId, toolId) })}
        />}
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

        {entered && media.width && media.height && <Box sx={{ display: toolId === "Crop" && !urlOptions.upscale ? "block" : "none" }}>
          <EditorCropControls
            key={media.id}
            image={cropImage}
            ratio={urlOptions.ratio}
            advanced={urlOptions.advanced}
            capture={captureStoreState}
            update={fn => updateStateRef.current?.(state => fn(state))}
            onOptions={options => patchEditor({
              ...(options.ratio !== undefined ? { ratio: options.ratio, preset: null } : {}),
              ...(options.advanced !== undefined ? { adv: options.advanced ? "1" : null } : {}),
            })}
            onChanged={() => { setHasChanges(true); setError(null); }}
            onFraming={framing => { patchEditor({ ratio: "free" }); void applyFramingPreset(framing); }}
          />
        </Box>}

        <Box ref={editorHostRef} onClickCapture={captureToolSelection} sx={{ ...buildEditorControlStyles(muiTheme), flex: 1, minHeight: 0, bgcolor: "background.default", position: "relative",
          [`& [${EDITOR_A11Y_ATTR}]:focus-visible`]: { outline: `2px solid ${muiTheme.palette.primary.main}`, outlineOffset: 2, borderRadius: "4px" } }}>
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
              defaultTabId={initialTool.current.tabId}
              defaultToolId={initialTool.current.toolId ?? "Crop"}
              loadableDesignState={initialDesignState}
              getCurrentImgDataFnRef={currentImgDataRef}
              updateStateFnRef={updateStateRef}
              onModify={(nextState) => {
                const nextDesignState = { ...nextState };
                delete nextDesignState.omoideFitRevision;
                setDesignState(nextDesignState);
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
            <Box sx={imageOverlay.clip ? { position: "absolute", pointerEvents: "none", zIndex: 5, left: imageOverlay.clip.left, top: imageOverlay.clip.top, width: imageOverlay.clip.width, height: imageOverlay.clip.height, overflow: "hidden" } : { display: "contents" }}>
            <Box sx={{ position: "absolute", pointerEvents: "none", zIndex: 5, left: imageOverlay.left - (imageOverlay.clip?.left ?? 0), top: imageOverlay.top - (imageOverlay.clip?.top ?? 0), width: imageOverlay.width, height: imageOverlay.height, overflow: "hidden" }}>
              {(faceSuggestions ?? []).map((suggestion) => (
                <FaceBoxOverlay key={suggestion.face_id} bbox={suggestion.face_bbox} sourceWidth={media.width!} sourceHeight={media.height!} />
              ))}
            </Box>
            </Box>
          )}
          {aspectGuides && imageOverlay && media.width && media.height && ((imageOverlay.rotation % 360) + 360) % 360 === 0 && (
            <Box sx={imageOverlay.clip ? { position: "absolute", pointerEvents: "none", zIndex: 5, left: imageOverlay.clip.left, top: imageOverlay.clip.top, width: imageOverlay.clip.width, height: imageOverlay.clip.height, overflow: "hidden" } : { display: "contents" }}>
            <Box sx={{ position: "absolute", pointerEvents: "none", zIndex: 5, left: imageOverlay.left - (imageOverlay.clip?.left ?? 0), top: imageOverlay.top - (imageOverlay.clip?.top ?? 0), width: imageOverlay.width, height: imageOverlay.height, overflow: "hidden" }}>
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
