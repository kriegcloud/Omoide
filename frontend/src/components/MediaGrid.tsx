import React, { useRef } from "react";
import { Box } from "@mui/material";
import { MediaPreview } from "../types";
import MediaCard from "./MediaCard";
import { useSelection } from "../context/SelectionContext";
import { useGridSelection } from "../hooks/useMarqueeSelection";
import MarqueeSelectionBox from "./MarqueeSelectionBox";

interface MediaGridProps {
  mediaItems: MediaPreview[];
  listKey: string;
}

export const MediaGrid: React.FC<MediaGridProps> = ({
  mediaItems,
  listKey,
}) => {
  const gridRef = useRef<HTMLDivElement>(null);
  const { isSelecting, selectedIds, setSelected, beginSelecting, clear } = useSelection();
  const { marqueeRect, onItemClick } = useGridSelection<number>({
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
  return (
    <Box
      ref={gridRef}
      sx={{
        display: "flex",
        flexWrap: "wrap",
        gap: 2, // Sets the spacing between cards
        position: "relative",
      }}
    >
      {mediaItems.map((media) => (
        <Box
          key={media.id}
          sx={{
            flex: "1 1 200px",
            maxWidth: "220px",
          }}
        >
          <MediaCard
            media={media}
            mediaListKey={listKey}
            onSelectionClick={onItemClick}
          />
        </Box>
      ))}
      <MarqueeSelectionBox container={gridRef.current} rect={marqueeRect} />
    </Box>
  );
};
