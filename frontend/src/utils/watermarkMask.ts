/** Map pointer coordinates to the un-oriented source raster, never the CSS size. */
export function rasterPoint(clientX: number, clientY: number, bounds: Pick<DOMRect, 'left' | 'top' | 'width' | 'height'>, width: number, height: number) {
  return { x: Math.max(0, Math.min(width - 1, (clientX - bounds.left) / bounds.width * width)), y: Math.max(0, Math.min(height - 1, (clientY - bounds.top) / bounds.height * height)) };
}
export function maskPngBase64(canvas: Pick<HTMLCanvasElement, 'width' | 'height' | 'toDataURL'>): string {
  if (!canvas.width || !canvas.height) throw new Error('Source raster not loaded');
  return canvas.toDataURL('image/png').split(',')[1];
}
/** Allocate the export backing store from source pixels; display bounds never enter here. */
export function createRasterMask(source: Pick<ImageBitmap, 'width' | 'height'>, initial: CanvasImageSource, create = () => document.createElement('canvas')): HTMLCanvasElement {
  const canvas = create(); canvas.width = source.width; canvas.height = source.height;
  const context = canvas.getContext('2d'); if (!context) throw new Error('Canvas unavailable');
  context.drawImage(initial, 0, 0); return canvas;
}
