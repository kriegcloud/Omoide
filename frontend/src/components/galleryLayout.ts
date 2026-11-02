/** Container-based masonry geometry. Gaps are included before dividing width. */
export function galleryLayout(width: number, minTileWidth = 260) {
  const available = Math.max(1, width);
  const gap = 4;
  const columns = Math.max(1, Math.min(minTileWidth < 260 ? 12 : 5, Math.floor((available + gap) / (Math.max(1, minTileWidth) + gap))));
  return { columns, gap, tileWidth: (available - gap * (columns - 1)) / columns };
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
