import React, { Children, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Box } from "@mui/material";
import Masonry from "react-masonry-css";
import { galleryFit, masonryPositions } from "./galleryLayout";
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
  useEffect(() => {
    if (!host) return;
    const measure = () => setSize({ width: Math.max(1, isVirtual ? viewportRef.current?.clientWidth ?? host.clientWidth : host.clientWidth), height: Math.max(320, window.innerHeight - 220) });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(host);
    if (isVirtual && viewportRef.current) observer.observe(viewportRef.current);
    window.addEventListener("resize", measure);
    return () => { observer.disconnect(); window.removeEventListener("resize", measure); };
  }, [host, isVirtual, viewportRef]);
  const childCount = Children.toArray(children).length;
  const { columns, maxWidth } = galleryFit(size.width, childCount, minTileWidth);
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
    <Box ref={hostRef} sx={{ width: "100%", minWidth: 0,
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
          <Masonry breakpointCols={columns} className="gallery-masonry" columnClassName="gallery-column">
            {children}
          </Masonry>
        </Box>
      )}
    </Box>
  );
}
