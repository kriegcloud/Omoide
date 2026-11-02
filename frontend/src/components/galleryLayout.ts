/** Widest media tile: a 9:16 portrait at this width (640 px tall) fits a laptop viewport. */
export const MAX_MEDIA_TILE_WIDTH = 360;

/** Container-based masonry geometry. Gaps are included before dividing width. */
export function galleryLayout(width: number, minTileWidth = 260) {
  const available = Math.max(1, width);
  const gap = 4;
  const media = minTileWidth >= 260;
  const byMin = Math.floor((available + gap) / (Math.max(1, minTileWidth) + gap));
  // Media galleries add columns on wide containers rather than growing tiles past the cap.
  const byMax = media ? Math.ceil((available + gap) / (MAX_MEDIA_TILE_WIDTH + gap)) : 1;
  const columns = Math.max(1, Math.min(12, Math.max(Math.min(media ? 5 : 12, byMin), byMax)));
  return { columns, gap, tileWidth: (available - gap * (columns - 1)) / columns };
}

/**
 * Masonry drops empty columns, so a short list would stretch each tile to fill the
 * row. Keep the full-row tile width and cap the container to the tiles present.
 */
export function galleryFit(width: number, count: number, minTileWidth = 260) {
  const layout = galleryLayout(width, minTileWidth);
  if (count <= 0 || count >= layout.columns) return { ...layout, maxWidth: null as number | null };
  return { ...layout, columns: count, maxWidth: count * layout.tileWidth + (count - 1) * layout.gap };
}

/** Same column-major placement as the masonry adapter, with bounded DOM windows. */
export function masonryPositions(width: number, ratios: number[]) {
  const { columns, tileWidth, gap } = galleryLayout(width);
  const heights = Array<number>(columns).fill(0);
  const positions = ratios.map((ratio, index) => {
    const column = index % columns;
    const height = tileWidth / (Number.isFinite(ratio) && ratio > 0 ? ratio : 1);
    const position = { left: column * (tileWidth + gap), top: heights[column], width: tileWidth, height };
    heights[column] += height + gap;
    return position;
  });
  return { positions, height: Math.max(0, ...heights) - (ratios.length ? gap : 0) };
}

/**
 * Masonry placement. Each new tile goes to the currently shortest column by
 * height (estimated from its aspect ratio, or measured after a render), so the
 * columns stay level and a newly loaded batch lands at the bottom of every
 * column instead of filling short columns far above the viewport.
 * Placement is sticky: a tile keeps its column across renders, so removing one
 * only moves the tiles below it in that column. A full re-deal only happens
 * when the column count changes.
 */
export function assignColumns(
  keys: readonly string[],
  columns: number,
  previous: ReadonlyMap<string, number> | null,
  heightOf: (key: string) => number | undefined = () => undefined,
): Map<string, number> {
  const count = Math.max(1, columns);
  const next = new Map<string, number>();
  const heights = Array<number>(count).fill(0);
  const known = keys.map(key => heightOf(key)).filter((h): h is number => Number.isFinite(h) && (h as number) > 0);
  const fallback = known.length ? known.reduce((a, b) => a + b, 0) / known.length : 1;
  const height = (key: string) => { const h = heightOf(key); return Number.isFinite(h) && (h as number) > 0 ? (h as number) : fallback; };
  if (previous) {
    for (const key of keys) {
      const column = previous.get(key);
      if (column !== undefined && column < count) { next.set(key, column); heights[column] += height(key); }
    }
  }
  for (const key of keys) {
    if (next.has(key)) continue;
    let target = 0;
    for (let column = 1; column < count; column++) if (heights[column] < heights[target] - 0.5) target = column;
    next.set(key, target); heights[target] += height(key);
  }
  return next;
}
