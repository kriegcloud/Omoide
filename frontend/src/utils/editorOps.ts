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
    };
    isFlippedX?: boolean;
    isFlippedY?: boolean;
    rotation?: number;
  };
  resize?: { width?: number; height?: number };
  shownImageDimensions?: {
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

export function designStateToOps(
  designState: FilerobotDesignState,
  imageWidth: number,
  imageHeight: number
): EditOp[] {
  const ops: EditOp[] = [];
  const adjustments = designState.adjustments;

  if (adjustments?.isFlippedX) {
    ops.push({ op: "flip", axis: "horizontal" });
  }
  if (adjustments?.isFlippedY) {
    ops.push({ op: "flip", axis: "vertical" });
  }

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
  }

  const rotatedWidth = rotation === 90 || rotation === 270 ? imageHeight : imageWidth;
  const rotatedHeight = rotation === 90 || rotation === 270 ? imageWidth : imageHeight;
  const crop = adjustments?.crop;
  const scaledBy = designState.shownImageDimensions?.scaledBy || 1;
  if (crop?.width && crop?.height) {
    const x = clamp(Math.round((crop.x ?? 0) / scaledBy), 0, rotatedWidth - 1);
    const y = clamp(Math.round((crop.y ?? 0) / scaledBy), 0, rotatedHeight - 1);
    const width = clamp(Math.round(crop.width / scaledBy), 1, rotatedWidth - x);
    const height = clamp(Math.round(crop.height / scaledBy), 1, rotatedHeight - y);
    const isFullImage = x === 0 && y === 0 && width === rotatedWidth && height === rotatedHeight;
    if (!isFullImage) ops.push({ op: "crop", x, y, width, height });
  }

  const resizeWidth = designState.resize?.width;
  const resizeHeight = designState.resize?.height;
  if (resizeWidth && resizeHeight) {
    ops.push({
      op: "resize",
      width: Math.max(1, Math.round(resizeWidth)),
      height: Math.max(1, Math.round(resizeHeight)),
    });
  }

  const brightness = brightnessToAdjustment(designState.finetunesProps?.brightness);
  const contrast = contrastToAdjustment(designState.finetunesProps?.contrast);
  const saturation = saturationToAdjustment(designState.finetunesProps?.saturation);
  if (brightness !== undefined || contrast !== undefined || saturation !== undefined) {
    ops.push({ op: "adjust", brightness, contrast, saturation });
  }

  return ops;
}

// Mental fixture: a 2000×1000 source shown at scaledBy=.25 has a displayed
// crop (x=125,y=50,w=250,h=125), which maps to source crop
// (x=500,y=200,w=1000,h=500). A 90° rotation swaps the clamp bounds first.
