import { Box } from "@mui/material";
import type { MarqueeRect } from "../hooks/useMarqueeSelection";

interface MarqueeSelectionBoxProps {
  container: HTMLElement | null;
  rect: MarqueeRect | null;
}

export default function MarqueeSelectionBox({
  container,
  rect,
}: MarqueeSelectionBoxProps) {
  if (!rect) return null;
  const containerRect = container?.getBoundingClientRect();
  return (
    <Box
      sx={{
        position: "absolute",
        pointerEvents: "none",
        zIndex: 100,
        border: "1px solid",
        borderColor: "primary.main",
        bgcolor: "rgba(25, 118, 210, 0.16)",
        left: rect.left - ((containerRect?.left ?? 0) + window.scrollX),
        top: rect.top - ((containerRect?.top ?? 0) + window.scrollY),
        width: rect.width,
        height: rect.height,
      }}
    />
  );
}
