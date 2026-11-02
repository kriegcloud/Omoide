/**
 * Where the editor's image is on screen, including filerobot's zoom and pan.
 *
 * filerobot (react-filerobot-image-editor 4.9) bundles its own Konva and only
 * exposes its store, so we replicate its transform from store values
 * (lib/components/MainCanvas/CanvasNode.js and Layers/DesignLayer/index.js):
 *  - stage: scale G = canvasScale * zoom.factor, offset (zoom.x, zoom.y);
 *    zoom is ignored while the Crop tool is active.
 *  - design layer (rotation 0): Crop tool -> image at (abstractX, abstractY),
 *    scale 1. Other tools -> image at x + crop-centring*R, scale R (resize).
 * Guides are hidden for rotated images, so rotation terms are zero here.
 */
export interface OverlayRect { left: number; top: number; width: number; height: number }
export interface ImageOverlayLayout extends OverlayRect { rotation: number; clip: OverlayRect | null }

interface RectLike { left: number; top: number; width: number; height: number }
interface CropLike { x?: number; y?: number; width?: number; height?: number }
export interface EditorStoreLike {
  toolId?: string;
  canvasScale?: number;
  zoom?: { factor?: number; x?: number | null; y?: number | null };
  shownImageDimensions?: { x?: number; y?: number; abstractX?: number; abstractY?: number; width?: number; height?: number };
  adjustments?: { rotation?: number; crop?: CropLike };
  resize?: { width?: number; height?: number };
  config?: { disableZooming?: boolean };
}

export const CROP_TOOL_ID = "Crop";

export function measureEditorImage(
  live: EditorStoreLike | null | undefined,
  hostRect: RectLike,
  stageRect: RectLike | null,
  canvasRect: RectLike | null = stageRect,
): ImageOverlayLayout | null {
  const shown = live?.shownImageDimensions;
  if (!live || !shown?.width || !shown.height || !stageRect) return null;
  const rotation = live.adjustments?.rotation ?? 0;
  const isCrop = live.toolId === CROP_TOOL_ID;
  const zoomAllowed = !isCrop && !live.config?.disableZooming;
  const factor = zoomAllowed ? live.zoom?.factor || 1 : 1;
  const scale = (live.canvasScale || 1) * factor;
  const panX = zoomAllowed ? live.zoom?.x || 0 : 0;
  const panY = zoomAllowed ? live.zoom?.y || 0 : 0;

  const crop = live.adjustments?.crop ?? {};
  const resize = live.resize ?? {};
  const rx = !isCrop && resize.width ? resize.width / (crop.width ?? shown.width) : 1;
  const ry = !isCrop && resize.height ? resize.height / (crop.height ?? shown.height) : 1;

  let x: number;
  let y: number;
  if (isCrop) {
    x = shown.abstractX ?? shown.x ?? 0;
    y = shown.abstractY ?? shown.y ?? 0;
  } else {
    x = (shown.x ?? 0) + (crop.width ? (shown.width / 2 - (crop.x ?? 0) - crop.width / 2) * rx : 0);
    y = (shown.y ?? 0) + (crop.height ? (shown.height / 2 - (crop.y ?? 0) - crop.height / 2) * ry : 0);
  }

  const clip = canvasRect
    ? { left: canvasRect.left - hostRect.left, top: canvasRect.top - hostRect.top, width: canvasRect.width, height: canvasRect.height }
    : null;
  return {
    left: stageRect.left - hostRect.left + panX + x * scale,
    top: stageRect.top - hostRect.top + panY + y * scale,
    width: shown.width * rx * scale,
    height: shown.height * ry * scale,
    rotation,
    clip,
  };
}

/** Same layout (within half a pixel)? Avoids re-rendering on every frame. */
export function sameOverlay(a: ImageOverlayLayout | null, b: ImageOverlayLayout | null): boolean {
  if (!a || !b) return a === b;
  const close = (x: number, y: number) => Math.abs(x - y) < 0.5;
  return close(a.left, b.left) && close(a.top, b.top) && close(a.width, b.width) && close(a.height, b.height)
    && a.rotation === b.rotation && JSON.stringify(a.clip) === JSON.stringify(b.clip);
}
