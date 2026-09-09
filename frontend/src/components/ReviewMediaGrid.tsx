import React from "react";
import { Box, CircularProgress, Paper } from "@mui/material";

import MarqueeSelectionBox from "./MarqueeSelectionBox";
import type { MarqueeRect } from "../hooks/useMarqueeSelection";

interface ReviewMediaGridProps {
  gridRef?: React.RefObject<HTMLDivElement | null>;
  marqueeRect?: MarqueeRect | null;
  itemCount: number;
  isLoading: boolean;
  hasMore: boolean;
  loaderRef: (node?: Element | null) => void;
  empty: React.ReactNode;
  children: React.ReactNode;
}

const ReviewMediaGrid: React.FC<ReviewMediaGridProps> = ({
  gridRef,
  marqueeRect = null,
  itemCount,
  isLoading,
  hasMore,
  loaderRef,
  empty,
  children,
}) => (
  <Paper variant="outlined" sx={{ mb: 3 }}>
    {isLoading && itemCount === 0 ? (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress />
      </Box>
    ) : itemCount === 0 ? (
      <Box sx={{ py: 6, textAlign: "center" }}>{empty}</Box>
    ) : (
      <Box
        ref={gridRef}
        sx={{
          position: "relative",
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
          gap: 1.5,
          p: 2,
        }}
      >
        {children}
        <MarqueeSelectionBox container={gridRef?.current ?? null} rect={marqueeRect} />
      </Box>
    )}
    {hasMore && <Box ref={loaderRef} sx={{ height: 1 }} />}
    {isLoading && itemCount > 0 && (
      <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
        <CircularProgress size={24} />
      </Box>
    )}
  </Paper>
);

export default ReviewMediaGrid;
