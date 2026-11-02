export interface CropSize { width: number; height: number }
export interface CropRect extends CropSize { x: number; y: number }

/** ISO paper uses the exact square-root ratio regardless of paper size. */
export function resolveCropRatio(value: string, image: CropSize, screen: CropSize): number | null {
  if (value === "free") return null;
  if (value === "current") return image.width / image.height;
  if (value === "screen") return screen.width / screen.height;
  if (value === "iso-landscape") return Math.SQRT2;
  if (value === "iso-portrait") return 1 / Math.SQRT2;
  if (value === "letter-landscape") return 11 / 8.5;
  if (value === "letter-portrait") return 8.5 / 11;
  const match = /^(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)$/.exec(value.trim());
  if (!match || !Number(match[1]) || !Number(match[2])) throw new Error("Use a positive W:H ratio, such as 4:5.");
  const ratio = Number(match[1]) / Number(match[2]);
  if (!Number.isFinite(ratio) || ratio <= 0) throw new Error("Use a positive W:H ratio, such as 4:5.");
  return ratio;
}

export function constrainCrop(rect: CropRect, bounds: CropSize): CropRect {
  if (![rect.x, rect.y, rect.width, rect.height, bounds.width, bounds.height].every(Number.isFinite) || bounds.width <= 0 || bounds.height <= 0) throw new Error("Crop geometry must contain finite dimensions.");
  const width = Math.max(1, Math.min(bounds.width, rect.width));
  const height = Math.max(1, Math.min(bounds.height, rect.height));
  return { x: Math.max(0, Math.min(bounds.width - width, rect.x)), y: Math.max(0, Math.min(bounds.height - height, rect.y)), width, height };
}

export function fitCrop(bounds: CropSize, ratio: number | null): CropRect {
  if (ratio == null) return { x: 0, y: 0, ...bounds };
  if (!Number.isFinite(ratio) || ratio <= 0) throw new Error("Crop ratio must be positive.");
  const width = Math.min(bounds.width, bounds.height * ratio), height = width / ratio;
  return { x: (bounds.width - width) / 2, y: (bounds.height - height) / 2, width, height };
}

export function cropToSource(rect: CropRect, image: CropSize, shown: CropSize): CropRect {
  return { x: rect.x * image.width / shown.width, y: rect.y * image.height / shown.height, width: rect.width * image.width / shown.width, height: rect.height * image.height / shown.height };
}
export function cropToDisplay(rect: CropRect, image: CropSize, shown: CropSize): CropRect {
  return cropToSource(rect, shown, image);
}

export interface CropStore {
  adjustments?: { crop?: Partial<CropRect> & { ratio?: string | number }; [key: string]: unknown };
  shownImageDimensions?: CropSize;
  designLayer?: { attrs?: { clipX?: number; clipY?: number; clipWidth?: number; clipHeight?: number } };
}
/** Read the same live clip fallback as the editor's save path. */
export function readCropFields(store: CropStore, image: CropSize): CropRect | null {
  const shown = store.shownImageDimensions;
  if (!shown?.width || !shown.height) return null;
  const crop = store.adjustments?.crop, clip = store.designLayer?.attrs;
  return cropToSource({ x: crop?.x ?? clip?.clipX ?? 0, y: crop?.y ?? clip?.clipY ?? 0, width: crop?.width ?? clip?.clipWidth ?? shown.width, height: crop?.height ?? clip?.clipHeight ?? shown.height }, image, shown);
}
/** Merge only crop geometry: flips, rotation and finetunes stay in the store. */
export function writeCropFields(store: CropStore, image: CropSize, rect: CropRect, ratio: number | null) {
  const shown = store.shownImageDimensions;
  if (!shown?.width || !shown.height) return null;
  return { isDesignState: true, tabId: "Adjust", toolId: "Crop", adjustments: { ...store.adjustments, crop: { ...store.adjustments?.crop, ...cropToDisplay(constrainCrop(rect, image), image, shown), ratio: ratio ?? "custom" } } };
}
