import Gallery from "./Gallery";
import { useListStore } from "../stores/useListStore";
import React, { useMemo, useRef } from "react";
import { Box } from "@mui/material";
import { MediaPreview } from "../types";
import MediaCard from "./MediaCard";
import { useSelection } from "../context/SelectionContext";
import { useGridSelection, type SelectionClickEvent } from "../hooks/useMarqueeSelection";
import MarqueeSelectionBox from "./MarqueeSelectionBox";

interface MediaGridProps {
  mediaItems: MediaPreview[];
  listKey: string;
}

export const MediaGrid: React.FC<MediaGridProps> = ({
  mediaItems,
  listKey,
}) => {
  const hasMore = useListStore((state) => state.lists[listKey]?.hasMore ?? false);
  const gridRef = useRef<HTMLDivElement>(null);
  const { isSelecting, selectedIds, setSelected, beginSelecting, clear } = useSelection();
  const orderedIds = useMemo(() => mediaItems.map(media => media.id), [mediaItems]);
  const { marqueeRect, onItemClick } = useGridSelection<number>({
    listKey,
    loadedCount: mediaItems.length,
    orderedIds,
    hasMore,
    containerRef: gridRef,
    itemSelector: "[data-selectable-id]",
    getId: (element) => Number(element.dataset.selectableId),
    selecting: isSelecting,
    allowPlainDragOnItems: false,
    onEnterSelection: beginSelecting,
    onExitSelection: clear,
    selectedIds,
    onSelectionChange: setSelected,
  });
  const virtualItems = useMediaGalleryItems(mediaItems, listKey, onItemClick);
  return (
    <Box ref={gridRef} sx={{ position: "relative" }}>
      <Gallery virtualItems={virtualItems} listKey={listKey} />
      <MarqueeSelectionBox container={gridRef.current} rect={marqueeRect} />
    </Box>
  );
};

/** Rendering extension for cursor adapters. The caller owns selection and paging. */
export function WindowedMediaGallery({ mediaItems, listKey, onSelectionClick, onEndReached }: MediaGridProps & {
  onSelectionClick: (id: number, event: SelectionClickEvent) => boolean;
  onEndReached?: () => void;
}) {
  const virtualItems = useMediaGalleryItems(mediaItems, listKey, onSelectionClick);
  return <Gallery virtualItems={virtualItems} listKey={listKey} onEndReached={onEndReached} />;
}

function useMediaGalleryItems(mediaItems: MediaPreview[], listKey: string,
  onItemClick: (id: number, event: SelectionClickEvent) => boolean) {
  return useMemo(() => mediaItems.map(media => ({
    id: media.id,
    // Match MediaCard's clamped preview height so absolute masonry cells do not overlap.
    aspectRatio: media.width && media.height
      ? 1 / Math.min(1.5, Math.max(0.65, media.height / media.width)) : 1,
    content: <MediaCard media={media} mediaListKey={listKey} onSelectionClick={onItemClick} />,
  })), [mediaItems, listKey, onItemClick]);
}
