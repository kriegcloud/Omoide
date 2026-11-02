import React, { Children, isValidElement, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Box } from "@mui/material";
import { assignColumns, galleryFit, masonryPositions } from "./galleryLayout";
import { useHotkeys } from "../hotkeys/useHotkey";
import { nextGridIndex } from "../hotkeys/GridKeyboardNavigation";

export interface VirtualGalleryItem {
  id: number;
  aspectRatio: number;
  content: React.ReactNode;
}
interface GalleryProps {
  children?: React.ReactNode;
  minTileWidth?: number;
  /** Windowing uses the same masonry geometry with a bounded scrollport. */
  virtualItems?: VirtualGalleryItem[];
  scrollRef?: React.RefObject<HTMLDivElement | null>;
  onEndReached?: () => void;
  listKey?: string;
}

/** Shared full-width masonry. Data, selection and paging stay in adapters. */
export default function Gallery({ children, virtualItems, scrollRef, onEndReached, listKey, minTileWidth = 260 }: GalleryProps) {
  const [host, setHost] = useState<HTMLDivElement | null>(null);
  const hostRef = useCallback((node: HTMLDivElement | null) => setHost(node), []);
  const ownScrollRef = useRef<HTMLDivElement>(null);
  const viewportRef = scrollRef ?? ownScrollRef;
  const frame = useRef<number | null>(null);
  const isVirtual = Boolean(virtualItems);
  const [size, setSize] = useState({ width: 1, height: 600 });
  const [scrollTop, setScrollTop] = useState(0);
  // Measure before paint: a gallery that mounts while scrolling (e.g. a virtualized
  // duplicate group) would otherwise paint one frame at width 1 -> one full-width
  // column, flashing each tile at full size.
  useLayoutEffect(() => {
    if (!host) return;
    const measure = () => setSize({ width: Math.max(1, isVirtual ? viewportRef.current?.clientWidth ?? host.clientWidth : host.clientWidth), height: Math.max(320, window.innerHeight - 220) });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(host);
    if (isVirtual && viewportRef.current) observer.observe(viewportRef.current);
    window.addEventListener("resize", measure);
    return () => { observer.disconnect(); window.removeEventListener("resize", measure); };
  }, [host, isVirtual, viewportRef]);
  const childItems = Children.toArray(children);
  const childCount = childItems.length;
  const { columns, maxWidth } = galleryFit(size.width, childCount, minTileWidth);
  const childKeys = childItems.map((child, index) => isValidElement(child) && child.key != null ? String(child.key) : `#${index}`);
  const keySignature = childKeys.join("\u0000");
  const [placement, setPlacement] = useState<{ columns: number; map: Map<string, number> } | null>(null);
  const placementMap = useMemo(
    () => assignColumns(childKeys, columns, placement?.columns === columns ? placement.map : null),
    // childKeys is derived from keySignature; placement is read only as the prior layout.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [keySignature, columns],
  );
  useEffect(() => {
    if (placement?.map !== placementMap) setPlacement({ columns, map: placementMap });
  }, [placementMap, columns, placement]);
  const columnChildren = useMemo(() => {
    const lists: React.ReactNode[][] = Array.from({ length: Math.max(1, columns) }, () => []);
    childItems.forEach((child, index) => lists[placementMap.get(childKeys[index]) ?? index % lists.length].push(child));
    return lists;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [children, placementMap, columns]);
  const virtualLayout = useMemo(() => masonryPositions(size.width, virtualItems?.map(item => item.aspectRatio) ?? []), [size.width, virtualItems]);
  const viewportHeight = Math.min(size.height, Math.max(1, virtualLayout.height));
  useEffect(() => {
    setScrollTop(0);
    if (viewportRef.current) viewportRef.current.scrollTop = 0;
  }, [listKey, viewportRef]);
  useEffect(() => {
    if (virtualItems?.length && scrollTop + viewportHeight >= virtualLayout.height - 300) onEndReached?.();
  }, [virtualItems, scrollTop, viewportHeight, virtualLayout.height, onEndReached]);
  useEffect(() => () => { if (frame.current !== null) cancelAnimationFrame(frame.current); }, []);
  useHotkeys(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].map(key => ({ key })), event => {
    if (!(event.target instanceof Element) || !virtualItems) return false;
    const tile = event.target.closest<HTMLElement>("[data-selectable-id]");
    if (!tile || !viewportRef.current?.contains(tile) || event.target.closest("button, input, textarea, select, [data-tile-control]")) return false;
    const index = virtualItems.findIndex(item => item.id === Number(tile.dataset.selectableId));
    const next = nextGridIndex(virtualLayout.positions, index, event.key);
    if (next < 0 || !virtualLayout.positions[next]) return false;
    const position = virtualLayout.positions[next];
    const top = Math.max(0, Math.min(position.top, virtualLayout.height - viewportHeight));
    viewportRef.current.scrollTop = top;
    setScrollTop(top);
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => {
      const viewport = viewportRef.current;
      if (!viewport) return;
      viewport.querySelectorAll<HTMLElement>("[data-selectable-id]").forEach(node => { node.tabIndex = Number(node.dataset.selectableId) === virtualItems[next].id ? 0 : -1; });
      viewport.querySelector<HTMLElement>(`[data-selectable-id="${virtualItems[next].id}"]`)?.focus({ preventScroll: true });
    });
  }, { scope: "page", enabled: Boolean(virtualItems), description: "Move focus between tiles" });
  return (
    <Box ref={hostRef} sx={{ width: "100%", minWidth: 0, visibility: size.width > 1 ? "visible" : "hidden",
      "& .gallery-masonry": { display: "flex", gap: "4px", width: "100%" },
      "& .gallery-column": { flex: "1 1 0", minWidth: 0 },
      "& .gallery-column > *": { marginBottom: "4px", minWidth: 0 },
    }}>
      {virtualItems ? (
        <Box ref={viewportRef} onScroll={event => setScrollTop(event.currentTarget.scrollTop)} sx={{ position: "relative", height: viewportHeight, overflow: "auto" }}>
          <Box sx={{ position: "relative", height: virtualLayout.height }}>
            {virtualItems.map((item, index) => {
              const position = virtualLayout.positions[index];
              if (position.top + position.height < scrollTop - 500 || position.top > scrollTop + viewportHeight + 500) return null;
              return <Box key={item.id} data-virtual-item={item.id} sx={{ position: "absolute", ...position }}>{item.content}</Box>;
            })}
          </Box>
        </Box>
      ) : (
        <Box sx={{ maxWidth: maxWidth ?? "none" }}>
          <div className="gallery-masonry">
            {columnChildren.map((column, index) => <div key={index} className="gallery-column">{column}</div>)}
          </div>
        </Box>
      )}
    </Box>
  );
}
