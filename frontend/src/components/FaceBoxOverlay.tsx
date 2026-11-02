import { Box } from "@mui/material";
import { faceBoxPercent, type FaceBoxSpace } from "../utils/faceBox";

interface FaceBoxOverlayProps {
  bbox?: readonly number[] | null;
  sourceWidth: number;
  sourceHeight: number;
  space?: FaceBoxSpace;
}

/** Shared face rectangle for upright gallery images and editor image guides. */
export default function FaceBoxOverlay({ bbox, sourceWidth, sourceHeight, space = "source" }: FaceBoxOverlayProps) {
  const rect = faceBoxPercent(bbox, sourceWidth, sourceHeight, space);
  if (!rect) return null;
  return <Box aria-hidden data-face-box sx={{ position: "absolute", pointerEvents: "none", boxSizing: "border-box", border: "2px solid", borderColor: "warning.light", boxShadow: "0 0 0 1px rgba(0,0,0,0.6)", left: `${rect.left}%`, top: `${rect.top}%`, width: `${rect.width}%`, height: `${rect.height}%` }} />;
}
