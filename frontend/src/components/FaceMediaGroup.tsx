import { Box, IconButton, Typography } from "@mui/material";
import VideoLibraryIcon from "@mui/icons-material/VideoLibrary";
import UnfoldMoreIcon from "@mui/icons-material/UnfoldMore";
import { useNavigate, useLocation } from "react-router-dom";
import { FaceRead } from "../types";
import { API } from "../config";
import { encodeFilePath } from "../urlUtils";
import SelectableTileFrame from "./SelectableTileFrame";
import type { SelectionClickEvent } from "../hooks/useMarqueeSelection";

interface FaceGroupCardProps {
  faces: FaceRead[];
  selectedFaceIds: number[];
  onSelectionClick: (faceIds: number[], event: SelectionClickEvent) => boolean;
  selecting: boolean;
  expanded: boolean;
  canMutate: boolean;
  onToggleExpand: () => void;
}

export default function FaceGroupCard({
  faces,
  selectedFaceIds,
  onSelectionClick,
  selecting,
  expanded,
  canMutate,
  onToggleExpand,
}: FaceGroupCardProps) {
  const navigate = useNavigate();
  const location = useLocation();

  const faceIds = faces.map((f) => f.id);
  const selectedCount = faceIds.filter((id) => selectedFaceIds.includes(id)).length;
  const allSelected = selectedCount === faces.length;
  const someSelected = selectedCount > 0 && !allSelected;

  const preview = faces.slice(0, 4);

  const handleCardClick = () => {
    navigate(`/medium/${faces[0].media_id}`, {
      state: { backgroundLocation: location },
    });
  };

  return (
    <SelectableTileFrame
      id={faces[0].id}
      data-selection-group
      keyboardReview
      selected={allSelected}
      indeterminate={someSelected}
      selecting={selecting}
      selectionEnabled={canMutate}
      onSelectionClick={(_, event) => onSelectionClick(faceIds, event)}
      onOpen={handleCardClick}
      aspectRatio={1}
      sx={{
        width: 140,
        height: 140,
        cursor: "pointer",
        flexShrink: 0,
        boxShadow: 2,
        ...(someSelected && { outline: "3px solid", outlineColor: "primary.light" }),
      }}
      bottomLeft={
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.4, bgcolor: "rgba(0,0,0,.6)", borderRadius: 1, p: 0.5 }}>
          <VideoLibraryIcon sx={{ fontSize: 13, color: "white" }} />
          <Typography variant="caption" sx={{ color: "white", fontWeight: 700, lineHeight: 1 }}>
            {faces.length}
          </Typography>
        </Box>
      }
      bottomRight={
        <IconButton
          data-tile-control
          data-no-marquee
          size="small"
          aria-label={expanded ? "Collapse faces" : "Expand faces"}
          aria-expanded={expanded}
          onClick={(e) => {
            e.stopPropagation();
            onToggleExpand();
          }}
          sx={{ color: "white", bgcolor: "rgba(0,0,0,.45)", borderRadius: "8px", "&:hover": { bgcolor: "rgba(0,0,0,.65)" } }}
        >
          <UnfoldMoreIcon sx={{ fontSize: 16 }} />
        </IconButton>
      }
    >
      {/* All collapsed faces share the collage bounds for marquee/range selection.
          Expanded faces supply their own tile bounds instead. */}
      {!expanded && faces.map((face) => (
        <Box key={face.id} data-selectable-id={face.id} aria-hidden sx={{ position: "absolute", inset: 0, pointerEvents: "none" }} />
      ))}
      {/* 2×2 thumbnail collage */}
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gridTemplateRows: "1fr 1fr",
          width: "100%",
          height: "100%",
          gap: "1px",
          bgcolor: "divider",
        }}
      >
        {Array.from({ length: 4 }).map((_, i) => {
          const face = preview[i] ?? preview[0];
          return (
            <Box
              key={i}
              component="img"
              loading="lazy"
              draggable={false}
              alt=""
              src={`${API}/thumbnails/${encodeFilePath(face.thumbnail_path)}`}
              sx={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
            />
          );
        })}
      </Box>
    </SelectableTileFrame>
  );
}
