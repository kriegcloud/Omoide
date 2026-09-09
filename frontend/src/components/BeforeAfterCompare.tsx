import { useState } from "react";
import { Box, Slider } from "@mui/material";
import type { Media } from "../types";
import { MediaImage } from "./MediaImage";
import { API } from "../config";
import { encodeFilePath } from "../urlUtils";

const source = (media: Media) => `${API}/originals/${encodeFilePath(media.path)}`;

export default function BeforeAfterCompare({ before, after }: { before: Media; after: Media }) {
  const [position, setPosition] = useState(50);
  return (
    <Box sx={{ position: "relative", width: "100%", mb: 2, overflow: "hidden", borderRadius: 2, bgcolor: "black" }}>
      <MediaImage src={source(after)} alt="Repaired" width={after.width || undefined} height={after.height || undefined} sx={{ display: "block", width: "100%", height: "auto", maxHeight: "80vh", objectFit: "contain" }} />
      <Box sx={{ position: "absolute", inset: 0, clipPath: `inset(0 ${100 - position}% 0 0)`, overflow: "hidden" }}>
        <MediaImage src={source(before)} alt="Original" width={before.width || undefined} height={before.height || undefined} sx={{ width: "100%", height: "100%", objectFit: "contain" }} />
      </Box>
      <Box sx={{ position: "absolute", top: 0, bottom: 0, left: `${position}%`, width: 2, bgcolor: "common.white", boxShadow: 2, pointerEvents: "none" }} />
      <Slider
        aria-label="Before and after divider"
        value={position}
        onChange={(_, value) => setPosition(value as number)}
        sx={{ position: "absolute", inset: 0, height: "100%", p: 0, opacity: 0, cursor: "ew-resize" }}
      />
    </Box>
  );
}
