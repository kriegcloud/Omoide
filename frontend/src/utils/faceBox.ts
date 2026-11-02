export type FaceBoxSpace = "source" | "detector";

/** Resolve raw EXIF dimensions against the upright thumbnail used for display. */
export function uprightFaceDimensions(width: number, height: number, naturalWidth: number, naturalHeight: number): [number, number] {
  return (width > height) !== (naturalWidth > naturalHeight) && width !== height && naturalWidth !== naturalHeight
    ? [height, width] : [width, height];
}

/** Normalize an xywh box; detector coordinates use integer-sized 1280px input. */
export function faceBoxPercent(bbox: readonly number[] | null | undefined, width: number, height: number, space: FaceBoxSpace = "source") {
  if (!bbox || bbox.length !== 4 || ![width, height, ...bbox].every(Number.isFinite) || width <= 0 || height <= 0 || bbox[2] <= 0 || bbox[3] <= 0) return null;
  const scale = space === "detector" ? Math.min(1, 1280 / Math.max(width, height)) : 1;
  const coordinateWidth = Math.floor(width * scale);
  const coordinateHeight = Math.floor(height * scale);
  if (!coordinateWidth || !coordinateHeight) return null;
  const [x, y, w, h] = bbox;
  const left = Math.max(0, x), top = Math.max(0, y);
  const right = Math.min(coordinateWidth, x + w), bottom = Math.min(coordinateHeight, y + h);
  if (right <= left || bottom <= top) return null;
  return { left: left / coordinateWidth * 100, top: top / coordinateHeight * 100, width: (right - left) / coordinateWidth * 100, height: (bottom - top) / coordinateHeight * 100 };
}
