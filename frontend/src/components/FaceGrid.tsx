import React, { useCallback, useMemo, useRef } from "react";
import { Box } from "@mui/material";
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
}

export const FaceGrid: React.FC<FaceGridProps> = ({
  faces,
  selectedFaceIds,
  onSelectionChange,
  selecting = selectedFaceIds.length > 0,
}) => {
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
  });

  return (
    <Box
      ref={containerRef}
      sx={{
        position: "relative",
        display: "flex",
        flexWrap: "wrap",
        gap: 2, // Consistent spacing
        justifyContent: "flex-start",
      }}
    >
      {faces.map((face) => (
        <FaceCard
          key={face.id}
          face={face}
          keyboardReview
          isProfile={false} // Orphans can't be profile pics
          selected={selectedIdSet.has(face.id)}
          selecting={selecting}
          onSelectionClick={onItemClick}
        />
      ))}
      <MarqueeSelectionBox container={containerRef.current} rect={marqueeRect} />
    </Box>
  );
};
