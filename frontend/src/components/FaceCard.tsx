import { parseServerDate } from "../utils/serverDate";
// components/FaceCard.tsx

import React from "react";
import {
  Box,
  IconButton,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { useNavigate, useLocation } from "react-router-dom";
import StarIcon from "@mui/icons-material/Star";
import AccessTimeIcon from "@mui/icons-material/AccessTime";
import { Face } from "../types";
import SelectableTileFrame from "./SelectableTileFrame";
import FaceBoxOverlay from "./FaceBoxOverlay";
import { useFaceThumbnail } from "../hooks/useFaceThumbnail";
import type { SelectionClickEvent } from "../hooks/useMarqueeSelection";

interface FaceCardProps {
  face: Face;
  isProfile: boolean;
  onSetProfile?: (faceId: number) => void;
  onChoose?: () => void;
  selected?: boolean;
  selecting?: boolean;
  keyboardReview?: boolean;
  onSelectionClick?: (faceId: number, event: SelectionClickEvent) => boolean;
  footer?: React.ReactNode;
}

function FaceCard({
  face,
  isProfile,
  onSetProfile,
  onChoose,
  selected = false,
  selecting = selected,
  keyboardReview = false,
  onSelectionClick,
  footer,
}: FaceCardProps) {
  const theme = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const { src: thumbUrl, showsParent, dimensions, aspectRatio, onLoad, onError } = useFaceThumbnail(face);

  const handleCardClick = () => {
    const reviewParams = location.pathname === "/orphanfaces" ? new URLSearchParams(location.search) : null;
    reviewParams?.set("item", String(face.id));
    navigate(`/medium/${face.media_id}${reviewParams ? `?${reviewParams}` : ""}`, {
      replace: !!location.state?.backgroundLocation,
      state: {
        backgroundLocation: location.state?.backgroundLocation || location,
        ...(face.timestamp != null && {
          sceneStart: face.timestamp,
          autoplayVideo: true,
        }),
      },
    });
  };

  return (
    <SelectableTileFrame
      id={face.id}
      selected={selected}
      selecting={selecting}
      keyboardReview={keyboardReview}
      selectionEnabled={Boolean(onSelectionClick)}
      onSelectionClick={onSelectionClick ?? (() => false)}
      onOpen={onChoose ?? handleCardClick}
      aspectRatio={aspectRatio}
      sx={{ width: "100%", cursor: "pointer", boxShadow: 2, ...(isProfile && { outline: `3px solid ${theme.palette.primary.main}`, outlineOffset: -3 }) }}
      footer={footer}
      menu={!isProfile && onSetProfile && (
        <Tooltip title="Set as profile">
          <IconButton
            size="small"
            aria-label="Set as profile"
            onClick={(e) => {
              e.stopPropagation();
              onSetProfile(face.id);
            }}
          >
            <StarIcon fontSize="small" sx={{ color: "accent.main" }} />
          </IconButton>
        </Tooltip>
      )}
      bottomLeft={typeof face.similarity === "number" ? (
        <Box sx={{ bgcolor: (theme) => alpha(theme.palette.common.black, 0.6), borderRadius: 1, px: 0.5, py: 0.25 }}>
          <Typography variant="caption" sx={{ color: "common.white", fontWeight: 600 }}>
            {`${face.similarity.toFixed(1)}%`}
          </Typography>
        </Box>
      ) : !showsParent ? (
        <Box sx={{ bgcolor: "rgba(0,0,0,.65)", borderRadius: 1, px: 0.5 }}>
          <Typography variant="caption" color="common.white">{face.timestamp != null ? "Video face crop" : "Face crop"}</Typography>
        </Box>
      ) : undefined}
      bottomRight={(face.timestamp != null || face.assigned_at) && (
        <Tooltip title={(
          <>
            {face.timestamp != null && (
              <div>Detected at {Math.floor(face.timestamp / 60)}:{String(Math.floor(face.timestamp % 60)).padStart(2, "0")} — click to jump</div>
            )}
            {face.assigned_at && (
              <div>Assigned {parseServerDate(face.assigned_at).toLocaleString()} · {face.assignment_source ?? "unknown"}</div>
            )}
          </>
        )}>
          <Box sx={{ bgcolor: (theme) => alpha(theme.palette.common.black, 0.6), borderRadius: "50%", p: 0.25, display: "flex" }}>
            <AccessTimeIcon sx={{ fontSize: 14, color: "common.white" }} />
          </Box>
        </Tooltip>
      )}
    >
      <Box
        component="img"
        src={thumbUrl}
        alt={showsParent ? face.timestamp != null ? "Video frame with detected face" : "Parent image with detected face" : "Detected face crop"}
        loading="lazy"
        decoding="async"
        draggable={false}
        onLoad={onLoad}
        onError={onError}
        sx={{ display: "block", width: "100%", height: "100%", objectFit: "contain", boxSizing: "border-box" }}
      />
      {showsParent && dimensions && <FaceBoxOverlay bbox={face.bbox} sourceWidth={dimensions[0]} sourceHeight={dimensions[1]} space="detector" />}
    </SelectableTileFrame>
  );
}

export default React.memo(FaceCard);
