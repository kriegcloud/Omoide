import { useSelection } from "../context/SelectionContext";
import React, { useCallback, useMemo, useRef } from "react";
import { Box } from "@mui/material";
import Gallery from "./Gallery";
import FaceCard from "./FaceCard";
import { FaceRead } from "../types";
import { useGridSelection } from "../hooks/useMarqueeSelection";
import MarqueeSelectionBox from "./MarqueeSelectionBox";
import { useRovingGridFocus } from "../hooks/useRovingGridFocus";

interface FaceGridProps {
  faces: FaceRead[];
  selectedFaceIds: number[];
  onSelectionChange: (ids: number[]) => void;
  selecting?: boolean;
  renderFooter?: (face: FaceRead) => React.ReactNode;
}

export const FaceGrid: React.FC<FaceGridProps> = ({
  faces,
  selectedFaceIds,
  onSelectionChange,
  selecting: selectingOverride,
  renderFooter,
}) => {
  const { isSelecting } = useSelection();
  const selecting = selectingOverride ?? (isSelecting || selectedFaceIds.length > 0);
  const selectedIdSet = useMemo(
    () => new Set(selectedFaceIds),
    [selectedFaceIds]
  );
  const containerRef = useRef<HTMLDivElement>(null);
  useRovingGridFocus(containerRef);
  const handleSelectionChange = useCallback(
    (ids: Set<number>) => onSelectionChange(Array.from(ids)),
    [onSelectionChange],
  );
  const { marqueeRect, onItemClick } = useGridSelection({
    containerRef,
    selectedIds: selectedIdSet,
    onSelectionChange: handleSelectionChange,
    selecting,
    orderedIds: faces.map((face) => face.id),
  });

  return (
    <Box
      ref={containerRef}
      sx={{ position: "relative" }}
    >
      <Gallery>
        {faces.map((face) => (
          <FaceCard
            key={face.id}
            face={face}
            keyboardReview
            isProfile={false} // Orphans can't be profile pics
            selected={selectedIdSet.has(face.id)}
            selecting={selecting}
            onSelectionClick={onItemClick}
            footer={renderFooter?.(face)}
          />
        ))}
      </Gallery>
      <MarqueeSelectionBox container={containerRef.current} rect={marqueeRect} />
    </Box>
  );
};
