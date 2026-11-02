import { Avatar, Box, type SxProps, type Theme } from "@mui/material";
import type { Face } from "../types";
import { useFaceThumbnail } from "../hooks/useFaceThumbnail";
import { faceBoxPercent } from "../utils/faceBox";

/** Reuse gallery source selection and detector-space geometry for a crisp crop. */
export default function PersonAvatar({ face }: { face?: Face | null }) {
  const thumbnail = useFaceThumbnail(face);
  const rect = thumbnail.showsParent && thumbnail.dimensions ? faceBoxPercent(face?.bbox, ...thumbnail.dimensions, "detector") : null;
  const dimensions = thumbnail.dimensions;
  const cropSize = rect && dimensions ? Math.min(dimensions[0] * rect.width / 100, dimensions[1] * rect.height / 100) : null;
  const crop: SxProps<Theme> = rect && dimensions && cropSize ? {
    position: "absolute", width: `${dimensions[0] / cropSize * 100}%`, height: `${dimensions[1] / cropSize * 100}%`, maxWidth: "none",
    left: `${50 - (rect.left + rect.width / 2) * dimensions[0] / cropSize}%`,
    top: `${50 - (rect.top + rect.height / 2) * dimensions[1] / cropSize}%`,
  } : { width: "100%", height: "100%", objectFit: "cover" };
  return <Box sx={{ position: "relative", width: { xs: 120, md: 160 }, height: { xs: 120, md: 160 }, borderRadius: "50%", overflow: "hidden", bgcolor: "action.hover", boxShadow: 3 }}>
    {!thumbnail.src ? <Avatar sx={{ width: "100%", height: "100%" }} /> : <Box component="img" src={thumbnail.src} alt="Profile photo" onLoad={thumbnail.onLoad} onError={thumbnail.onError} sx={crop} />}
  </Box>;
}
