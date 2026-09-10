import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { FixedSizeGrid, type GridOnItemsRenderedProps } from "react-window";
import { useHotkeys } from "../hotkeys/useHotkey";

/** Arrow movement uses the complete loaded order, including cells outside the DOM. */
export function nextWindowedIndex(index: number, key: string, columns: number, count: number) {
  if (index < 0 || count === 0) return -1;
  if (key === "ArrowLeft") return Math.max(0, index - 1);
  if (key === "ArrowRight") return Math.min(count - 1, index + 1);
  if (key === "ArrowUp") return index >= columns ? index - columns : index;
  if (key === "ArrowDown") return index + columns < count
    ? index + columns : Math.floor(index / columns) < Math.floor((count - 1) / columns) ? count - 1 : index;
  return index;
}

export function windowedGridLayout(width: number, count: number, viewportHeight: number) {
  const columns = Math.max(2, Math.min(5, Math.floor(width / 230)));
  const cellSize = Math.max(1, width / columns);
  const rows = Math.ceil(count / columns);
  return { columns, cellSize, rows, height: Math.max(1, Math.min(rows * cellSize, Math.max(320, viewportHeight - 220))) };
}

/** A bounded react-window scrollport with page-scope keyboard focus and lazy paging. */
export function useWindowedGrid({ ids, containerRef, loadMore, hasMore, loading, error, listKey }: {
  ids: number[];
  containerRef: RefObject<HTMLDivElement | null>;
  loadMore: () => void;
  hasMore: boolean;
  loading: boolean;
  error: string | null;
  listKey: string;
}) {
  const [host, setHost] = useState<HTMLDivElement | null>(null);
  const hostRef = useCallback((node: HTMLDivElement | null) => setHost(node), []);
  const gridRef = useRef<FixedSizeGrid>(null);
  const focusFrameRef = useRef<number | null>(null);
  const [size, setSize] = useState({ width: 1000, viewportHeight: 800 });
  const layout = useMemo(() => windowedGridLayout(size.width, ids.length, size.viewportHeight), [size, ids.length]);
  useEffect(() => {
    if (!host) return;
    const measure = () => {
      const width = Math.max(1, host.clientWidth);
      const viewportHeight = window.innerHeight;
      setSize(previous => previous.width === width && previous.viewportHeight === viewportHeight
        ? previous : { width, viewportHeight });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(host);
    window.addEventListener("resize", measure);
    return () => { observer.disconnect(); window.removeEventListener("resize", measure); };
  }, [host]);
  useEffect(() => { gridRef.current?.scrollTo({ scrollLeft: 0, scrollTop: 0 }); }, [listKey]);
  useEffect(() => () => {
    if (focusFrameRef.current !== null) cancelAnimationFrame(focusFrameRef.current);
  }, []);

  const isGridTarget = useCallback((event: KeyboardEvent) => {
    if (!(event.target instanceof Element) || event.target.closest("button, input, textarea, select, [data-tile-control]")) return false;
    const tile = event.target.closest<HTMLElement>("[data-selectable-id]");
    return Boolean(tile && containerRef.current?.contains(tile));
  }, [containerRef]);
  useHotkeys(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].map(key => ({ key })), event => {
    const tile = (event.target as Element).closest<HTMLElement>("[data-selectable-id]");
    const index = ids.indexOf(Number(tile?.dataset.selectableId));
    const next = nextWindowedIndex(index, event.key, layout.columns, ids.length);
    if (next < 0) return false;
    gridRef.current?.scrollToItem({ rowIndex: Math.floor(next / layout.columns), columnIndex: next % layout.columns, align: "smart" });
    if (focusFrameRef.current !== null) cancelAnimationFrame(focusFrameRef.current);
    focusFrameRef.current = requestAnimationFrame(() => {
      const container = containerRef.current;
      if (!container) return;
      container.querySelectorAll<HTMLElement>("[data-selectable-id]").forEach(node => {
        node.tabIndex = Number(node.dataset.selectableId) === ids[next] ? 0 : -1;
      });
      container.querySelector<HTMLElement>(`[data-selectable-id="${ids[next]}"]`)?.focus({ preventScroll: true });
    });
  }, { scope: "page", when: isGridTarget, description: "Move focus between tiles" });

  const onItemsRendered = useCallback(({ visibleRowStopIndex }: GridOnItemsRenderedProps) => {
    if (hasMore && !loading && !error && visibleRowStopIndex >= layout.rows - 2) loadMore();
  }, [hasMore, loading, error, layout.rows, loadMore]);
  return { hostRef, gridRef, onItemsRendered, width: size.width, ...layout };
}
