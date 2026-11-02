import { useCallback, useLayoutEffect, useMemo, useRef, useState, type RefObject } from "react";

export function rowOffsets(count: number, estimate: number, heights: ReadonlyMap<number, number>): number[] {
  const offsets = [0];
  for (let index = 0; index < count; index++) offsets.push(offsets[index] + (heights.get(index) ?? estimate));
  return offsets;
}

/** First row whose bottom is strictly beyond y; count is the past-the-end sentinel. */
function firstRow(offsets: number[], y: number): number {
  let low = 0, high = offsets.length - 1;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (offsets[middle + 1] <= y) low = middle + 1;
    else high = middle;
  }
  return low;
}

export function virtualItems(offsets: number[], scrollY: number, listTop: number, height: number, overscan: number) {
  const end = scrollY - listTop + height + overscan;
  const items: { index: number; top: number }[] = [];
  for (let index = firstRow(offsets, scrollY - listTop - overscan);
    index < offsets.length - 1 && offsets[index] < end; index++) {
    items.push({ index, top: offsets[index] });
  }
  return items;
}

export function anchorDelta(previous: number[], next: number[], visibleTop: number): number {
  if (visibleTop < 0) return 0;
  const anchor = firstRow(previous, visibleTop);
  if (anchor >= previous.length - 1 || anchor >= next.length - 1) return 0;
  return next[anchor] - previous[anchor];
}

type Align = "start" | "center" | "end" | "smart";
export function indexScrollTop(offsets: number[], index: number, align: Align, listTop: number,
  scrollY: number, height: number, header: number): number {
  const row = Math.max(0, Math.min(index, offsets.length - 2));
  const start = listTop + (offsets[row] ?? 0) - header;
  const end = listTop + (offsets[row + 1] ?? 0) - height;
  if (align === "smart") {
    // A tall row is already usable when the viewport is inside it.
    if (scrollY >= Math.min(start, end) && scrollY <= Math.max(start, end)) return scrollY;
    return Math.max(0, start);
  }
  return Math.max(0, align === "end" ? end : align === "center" ? (start + end) / 2 : start);
}

interface Options {
  containerRef: RefObject<HTMLElement | null>;
  rowCount: number;
  estimatedHeight: number;
  measuredHeights: ReadonlyMap<number, number>;
  overscan: number;
  headerOffset?: number;
  /** Change on reorder/filter/width reset, where old indices no longer identify anchors. */
  layoutKey?: string;
}

export function useWindowVirtualRows({ containerRef, rowCount, estimatedHeight, measuredHeights,
  overscan, headerOffset = 0, layoutKey = "" }: Options) {
  const offsets = useMemo(() => rowOffsets(rowCount, estimatedHeight, measuredHeights),
    [rowCount, estimatedHeight, measuredHeights]);
  const [viewport, setViewport] = useState({ scrollY: window.scrollY, height: window.innerHeight, listTop: 0 });
  const readViewport = useCallback(() => {
    const listTop = (containerRef.current?.getBoundingClientRect().top ?? 0) + window.scrollY;
    setViewport(previous => previous.scrollY === window.scrollY && previous.height === window.innerHeight &&
      previous.listTop === listTop ? previous : { scrollY: window.scrollY, height: window.innerHeight, listTop });
  }, [containerRef]);

  useLayoutEffect(() => {
    let frame: number | null = null;
    const schedule = () => {
      if (frame === null) frame = window.requestAnimationFrame(() => { frame = null; readViewport(); });
    };
    readViewport();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    const observer = new ResizeObserver(schedule);
    if (containerRef.current) {
      observer.observe(containerRef.current);
      if (containerRef.current.parentElement) observer.observe(containerRef.current.parentElement);
    }
    return () => {
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      observer.disconnect();
      if (frame !== null) window.cancelAnimationFrame(frame);
    };
  }, [containerRef, readViewport]);

  const previous = useRef({ offsets, layoutKey });
  useLayoutEffect(() => {
    const listTop = (containerRef.current?.getBoundingClientRect().top ?? 0) + window.scrollY;
    if (previous.current.layoutKey === layoutKey) {
      const delta = anchorDelta(previous.current.offsets, offsets, window.scrollY + headerOffset - listTop);
      if (delta) window.scrollBy({ top: delta, behavior: "instant" });
    }
    previous.current = { offsets, layoutKey };
    readViewport();
  }, [offsets, layoutKey, headerOffset, containerRef, readViewport]);

  // Focus effects depend on this callback: measurements must not steal window
  // scroll back to a focused group after the user has scrolled away.
  const scrollState = useRef({ offsets, headerOffset });
  scrollState.current = { offsets, headerOffset };
  const scrollToIndex = useCallback((index: number, align: Align = "smart") => {
    const { offsets, headerOffset } = scrollState.current;
    const listTop = (containerRef.current?.getBoundingClientRect().top ?? 0) + window.scrollY;
    window.scrollTo({ top: indexScrollTop(offsets, index, align, listTop, window.scrollY, window.innerHeight, headerOffset), behavior: "instant" });
    readViewport();
  }, [containerRef, readViewport]);

  return {
    totalHeight: offsets[offsets.length - 1],
    items: virtualItems(offsets, viewport.scrollY, viewport.listTop, viewport.height, overscan),
    scrollToIndex,
  };
}
