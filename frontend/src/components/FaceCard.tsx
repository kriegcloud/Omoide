// components/FaceCard.tsx

import React from "react";
import {
  Avatar,
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
import { API } from "../config";
import { Face } from "../types";
import { encodeFilePath } from "../urlUtils";
import SelectableTileFrame from "./SelectableTileFrame";
import type { SelectionClickEvent } from "../hooks/useMarqueeSelection";

interface FaceCardProps {
  face: Face;
  isProfile: boolean;
  onSetProfile?: (faceId: number) => void;
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
  selected = false,
  selecting = selected,
  keyboardReview = false,
  onSelectionClick,
  footer,
}: FaceCardProps) {
  const theme = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const thumbUrl = `${API}/thumbnails/${encodeFilePath(face.thumbnail_path)}`;

  const handleCardClick = () => {
    navigate(`/medium/${face.media_id}`, {
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
      onOpen={handleCardClick}
      aspectRatio={1}
      sx={{ width: 140, height: footer ? "auto" : 140, flexShrink: 0, cursor: "pointer", boxShadow: 2 }}
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
      bottomLeft={typeof face.similarity === "number" && (
        <Box sx={{ bgcolor: (theme) => alpha(theme.palette.common.black, 0.6), borderRadius: 1, px: 0.5, py: 0.25 }}>
          <Typography variant="caption" sx={{ color: "common.white", fontWeight: 600 }}>
            {`${face.similarity.toFixed(1)}%`}
          </Typography>
        </Box>
      )}
      bottomRight={(face.timestamp != null || face.assigned_at) && (
        <Tooltip title={(
          <>
            {face.timestamp != null && (
              <div>Detected at {Math.floor(face.timestamp / 60)}:{String(Math.floor(face.timestamp % 60)).padStart(2, "0")} — click to jump</div>
            )}
            {face.assigned_at && (
              <div>Assigned {new Date(`${face.assigned_at}Z`).toLocaleString()} · {face.assignment_source ?? "unknown"}</div>
            )}
          </>
        )}>
          <Box sx={{ bgcolor: (theme) => alpha(theme.palette.common.black, 0.6), borderRadius: "50%", p: 0.25, display: "flex" }}>
            <AccessTimeIcon sx={{ fontSize: 14, color: "common.white" }} />
          </Box>
        </Tooltip>
      )}
    >
      <Avatar
        src={thumbUrl}
        variant="rounded"
        slotProps={{ img: { loading: "lazy", draggable: false } }}
        sx={{
          width: "100%",
          height: "100%",
          boxSizing: "border-box",
          borderRadius: 3,
          border: isProfile ? `3px solid ${theme.palette.primary.main}` : "none",
        }}
      />
    </SelectableTileFrame>
  );
}

export default React.memo(FaceCard);
