export type EditOp =
  | { op: "rotate"; degrees: 90 | 180 | 270 }
  | { op: "flip"; axis: "horizontal" | "vertical" }
  | { op: "crop"; x: number; y: number; width: number; height: number }
  | { op: "resize"; width: number; height: number }
  | {
      op: "adjust";
      brightness?: number;
      contrast?: number;
      saturation?: number;
    };

export function describeEditOps(ops: EditOp[]): string {
  return ops
    .map((op) => {
      switch (op.op) {
        case "crop":
          return `Crop ${op.width}×${op.height}`;
        case "rotate":
          return `Rotate ${op.degrees}°`;
        case "flip":
          return `Flip ${op.axis}`;
        case "resize":
          return `Resize ${op.width}×${op.height}`;
        case "adjust": {
          const adjustments = [
            op.brightness !== undefined && "brightness",
            op.contrast !== undefined && "contrast",
            op.saturation !== undefined && "saturation",
          ].filter(Boolean);
          return `Adjust ${adjustments.join(", ")}`;
        }
      }
    })
    .join(", ");
}

export interface FilerobotDesignState {
  finetunes?: string[];
  finetunesProps?: {
    brightness?: number;
    contrast?: number;
    saturation?: number;
  };
  adjustments?: {
    crop?: {
      x?: number;
      y?: number;
      width?: number;
      height?: number;
      ratio?: number | string;
    };
    isFlippedX?: boolean;
    isFlippedY?: boolean;
    rotation?: number;
  };
  resize?: { width?: number; height?: number };
  shownImageDimensions?: {
    x?: number;
    y?: number;
    width: number;
    height: number;
    scaledBy: number;
  };
  [key: string]: unknown;
}

export class UnsupportedEditorRotationError extends Error {
  constructor(rotation: number) {
    super(
      `Rotation must be a right angle. Reset the ${rotation.toFixed(1)}° rotation and use Rotate left or Rotate right.`
    );
    this.name = "UnsupportedEditorRotationError";
  }
}

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));

const omitNeutral = (value: number): number | undefined =>
  Math.abs(value) < 0.0001 ? undefined : Math.round(clamp(value, -100, 100));

const brightnessToAdjustment = (
  value: number | undefined
): number | undefined => {
  if (value === undefined || Math.abs(value) < 0.0001) return undefined;
  return omitNeutral(clamp(value, -1, 1) * 100);
};

const contrastToAdjustment = (value: number | undefined) =>
  value === undefined ? undefined : omitNeutral(value);

const saturationToAdjustment = (value: number | undefined) => {
  if (value === undefined || Math.abs(value) < 0.0001) return undefined;
  return omitNeutral(value < 0 ? (value / 2) * 100 : (value / 10) * 100);
};

// Source: node_modules/react-filerobot-image-editor/lib/index.js in 4.9.1.
// Finetune constants are brightness -1..1, contrast -100..100, and HSV
// saturation -2..10. Saturation is mapped piecewise so its neutral 0 stays 0.

/** Filerobot 4.9.1 metadata keeps pre-rotation crop dimensions. Its rendered
 * canvas is the output truth; normalize backing pixels by the saving ratio. */
export function editorCanvasOutput(imageData?: {imageCanvas?: {width: number; height: number}}, pixelRatio = 1): {width: number; height: number} | undefined {
  const canvas = imageData?.imageCanvas;
  if (!canvas || !Number.isFinite(pixelRatio) || pixelRatio <= 0 || !Number.isFinite(canvas.width) || !Number.isFinite(canvas.height) || canvas.width <= 0 || canvas.height <= 0) return undefined;
  return {width: Math.round(canvas.width / pixelRatio), height: Math.round(canvas.height / pixelRatio)};
}

/** Preserve a crop's source coordinates when Filerobot fits its canvas again.
 * 4.9.1 updates shownImageDimensions without scaling an unchanged crop box.
 * A changed crop is a new user edit already expressed in the current space. */
export function refitEditorCrop(previous: FilerobotDesignState | null, current: FilerobotDesignState): FilerobotDesignState["adjustments"] | undefined {
  const before = previous?.shownImageDimensions;
  const after = current.shownImageDimensions;
  const crop = current.adjustments?.crop;
  const oldCrop = previous?.adjustments?.crop;
  if (!before?.width || !before.height || !after?.width || !after.height || !crop || !oldCrop ||
      (before.width === after.width && before.height === after.height) ||
      (["x", "y", "width", "height"] as const).some(key => crop[key] !== oldCrop[key])) return undefined;
  const sx = after.width / before.width;
  const sy = after.height / before.height;
  return { ...current.adjustments, crop: { ...crop,
    ...(crop.x !== undefined ? { x: crop.x * sx } : {}),
    ...(crop.y !== undefined ? { y: crop.y * sy } : {}),
    ...(crop.width !== undefined ? { width: crop.width * sx } : {}),
    ...(crop.height !== undefined ? { height: crop.height * sy } : {}),
  } };
}

export interface FilerobotFitState extends FilerobotDesignState {
  /** Internal provider-store acknowledgement; never a serialized edit field. */
  omoideFitRevision?: number;
  pastDesignStates?: FilerobotDesignState[];
  futureDesignStates?: FilerobotDesignState[];
}

/** Keep every undo/redo entry in the current display coordinate space. */
export function refitEditorState(previous: FilerobotDesignState | null, current: FilerobotFitState): Partial<FilerobotFitState> | undefined {
  const before = previous?.shownImageDimensions;
  const after = current.shownImageDimensions;
  if (!before?.width || !before.height || !after?.width || !after.height ||
      (before.width === after.width && before.height === after.height)) return undefined;
  const history = (entries: FilerobotDesignState[]) => entries.map(entry => {
    const adjustments = refitEditorCrop(
      { ...entry, shownImageDimensions: before },
      { ...entry, shownImageDimensions: after },
    );
    return adjustments ? { ...entry, adjustments } : entry;
  });
  const adjustments = refitEditorCrop(previous, current);
  return {
    ...(adjustments ? { adjustments } : {}),
    ...(current.pastDesignStates ? { pastDesignStates: history(current.pastDesignStates) } : {}),
    ...(current.futureDesignStates ? { futureDesignStates: history(current.futureDesignStates) } : {}),
  };
}

export class EditorOutputMismatchError extends Error {
  constructor(expected: { width: number; height: number }, actual: { width: number; height: number }) {
    super(
      `The editor expects a ${expected.width}×${expected.height} result but the replay would produce ${actual.width}×${actual.height}. Reset the edit and try again.`
    );
    this.name = "EditorOutputMismatchError";
  }
}

/**
 * Convert Filerobot's designState into the ordered op list the backend
 * replays with Pillow. Mirrors Filerobot's own save path
 * (hooks/useTransformedImgData.js in 4.9.1):
 *
 *   1. flips live on the image node, so the crop box is expressed over the
 *      already-flipped picture → flip first;
 *   2. the crop box is in "shown image" space (un-rotated) and is mapped to
 *      source pixels by original/shown (mapCropBox) → crop second, before
 *      rotation. `scaledBy` is not a reliable factor (it reports the canvas
 *      zoom, not shown/original), so the ratio is derived from the sizes;
 *   3. the stage is then rotated (Konva: positive = clockwise);
 *   4. resize is applied to the rotated result;
 *   5. finetunes are colour-only and commute with geometry.
 *
 * `expectedOutput` is the rendered canvas size from getCurrentImgData
 * (its width/height metadata ignores rotation). When present the computed geometry
 * must agree with it, otherwise we refuse rather than write a wrong file.
 */
export function designStateToOps(
  designState: FilerobotDesignState,
  imageWidth: number,
  imageHeight: number,
  expectedOutput?: { width?: number; height?: number }
): EditOp[] {
  const ops: EditOp[] = [];
  const adjustments = designState.adjustments;

  if (adjustments?.isFlippedX) {
    ops.push({ op: "flip", axis: "horizontal" });
  }
  if (adjustments?.isFlippedY) {
    ops.push({ op: "flip", axis: "vertical" });
  }

  // Crop, in un-rotated source pixels.
  let width = imageWidth;
  let height = imageHeight;
  const crop = adjustments?.crop as
    | (NonNullable<NonNullable<FilerobotDesignState["adjustments"]>["crop"]> & {
        noEffect?: boolean;
      })
    | undefined;
  const shown = designState.shownImageDimensions;
  const sx = shown?.width ? imageWidth / shown.width : 1;
  const sy = shown?.height ? imageHeight / shown.height : 1;
  if (crop && !crop.noEffect && crop.width && crop.height) {
    const x = clamp(Math.round((crop.x ?? 0) * sx), 0, imageWidth - 1);
    const y = clamp(Math.round((crop.y ?? 0) * sy), 0, imageHeight - 1);
    const w = clamp(Math.round(crop.width * sx), 1, imageWidth - x);
    const h = clamp(Math.round(crop.height * sy), 1, imageHeight - y);
    const isFullImage = x === 0 && y === 0 && w === imageWidth && h === imageHeight;
    if (!isFullImage) {
      ops.push({ op: "crop", x, y, width: w, height: h });
      width = w;
      height = h;
    }
  }

  // Rotation (right angles only; Konva positive = clockwise).
  const rawRotation = adjustments?.rotation ?? 0;
  const normalizedRotation = ((rawRotation % 360) + 360) % 360;
  const snappedRotation = Math.round(normalizedRotation / 90) * 90;
  const rotationDelta = Math.min(
    Math.abs(normalizedRotation - snappedRotation),
    Math.abs(normalizedRotation - (snappedRotation - 360))
  );
  if (rotationDelta > 0.5) {
    throw new UnsupportedEditorRotationError(rawRotation);
  }
  const rotation = snappedRotation % 360;
  if (rotation === 90 || rotation === 180 || rotation === 270) {
    ops.push({ op: "rotate", degrees: rotation });
    if (rotation !== 180) [width, height] = [height, width];
  }

  // Resize of the rotated result.
  const resizeWidth = designState.resize?.width;
  const resizeHeight = designState.resize?.height;
  if (resizeWidth && resizeHeight) {
    width = Math.max(1, Math.round(resizeWidth));
    height = Math.max(1, Math.round(resizeHeight));
    ops.push({ op: "resize", width, height });
  }

  if (
    expectedOutput?.width &&
    expectedOutput?.height &&
    (Math.abs(expectedOutput.width - width) > 2 ||
      Math.abs(expectedOutput.height - height) > 2)
  ) {
    throw new EditorOutputMismatchError(
      { width: expectedOutput.width, height: expectedOutput.height },
      { width, height }
    );
  }

  const brightness = brightnessToAdjustment(designState.finetunesProps?.brightness);
  const contrast = contrastToAdjustment(designState.finetunesProps?.contrast);
  const saturation = saturationToAdjustment(designState.finetunesProps?.saturation);
  if (brightness !== undefined || contrast !== undefined || saturation !== undefined) {
    ops.push({ op: "adjust", brightness, contrast, saturation });
  }

  return ops;
}

// Fixture: a 2000×1000 source shown at 500×250 with a displayed crop
// (x=125, y=50, w=250, h=125) maps to the source crop (x=500, y=200,
// w=1000, h=500); a following 90° rotation yields a 500×1000 result.
