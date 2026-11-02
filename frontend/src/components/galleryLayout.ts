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
 * Sticky column placement for the masonry. Round-robin by index (react-masonry-css)
 * moves every later tile to another column when one tile is removed or inserted,
 * so a delete reshuffles the whole grid. Here a tile keeps its column across
 * renders; new tiles go to the column with the fewest tiles. A full re-deal only
 * happens when the column count changes or columns drift too far apart.
 */
export function assignColumns(
  keys: readonly string[],
  columns: number,
  previous: ReadonlyMap<string, number> | null,
  maxImbalance = 3,
): Map<string, number> {
  const count = Math.max(1, columns);
  const roundRobin = () => new Map(keys.map((key, index) => [key, index % count] as const));
  if (!previous) return roundRobin();
  const next = new Map<string, number>();
  const sizes = Array<number>(count).fill(0);
  for (const key of keys) {
    const column = previous.get(key);
    if (column !== undefined && column < count) { next.set(key, column); sizes[column]++; }
  }
  for (const key of keys) {
    if (next.has(key)) continue;
    let target = 0;
    for (let column = 1; column < count; column++) if (sizes[column] < sizes[target]) target = column;
    next.set(key, target); sizes[target]++;
  }
  return Math.max(...sizes) - Math.min(...sizes) > maxImbalance ? roundRobin() : next;
}
