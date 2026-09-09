import React from "react";
import { Box, Chip, Typography } from "@mui/material";
import { Link, useLocation } from "react-router-dom";

import { API } from "../config";
import { encodeFilePath } from "../urlUtils";
import SelectableTileFrame from "./SelectableTileFrame";
import type { SelectionClickEvent } from "../hooks/useMarqueeSelection";

export interface SelectableMediaTileProps {
  id: number;
  filename: string;
  thumbnailPath: string | null;
  caption: string;
  selected: boolean;
  badgeLabel?: string;
  badgeColor?: "default" | "primary" | "warning" | "error";
  selecting?: boolean;
  onSelectionClick?: (id: number, event: SelectionClickEvent) => boolean;
  /** @deprecated Pass onSelectionClick from useGridSelection instead. */
  onToggle?: (id: number) => void;
}

const SelectableMediaTile: React.FC<SelectableMediaTileProps> = ({
  id,
  filename,
  thumbnailPath,
  caption,
  selected,
  selecting = selected,
  onSelectionClick,
  badgeLabel,
  badgeColor = "default",
  onToggle,
}) => {
  const location = useLocation();
  const thumbUrl = `${API}/thumbnails/${thumbnailPath ? encodeFilePath(thumbnailPath) : `${id}.jpg`}`;

  const handleSelectionClick = (itemId: number, event: SelectionClickEvent) => {
    if (onSelectionClick) return onSelectionClick(itemId, event);
    if (onToggle && (selecting || event.ctrlKey || event.metaKey || event.shiftKey)) {
      onToggle(itemId);
      return true;
    }
    return false;
  };

  return (
    <SelectableTileFrame
      id={id}
      selected={selected}
      selecting={selecting}
      selectionEnabled={Boolean(onSelectionClick || onToggle)}
      onSelectionClick={handleSelectionClick}
      href={`/medium/${id}`}
      linkState={{ backgroundLocation: location }}
      aspectRatio="4/3"
      sx={{ minWidth: 0, cursor: "pointer" }}
      bottomRight={badgeLabel && (
        <Chip
          component={Link}
          to={`/medium/${id}`}
          state={{ backgroundLocation: location }}
          tabIndex={-1}
          draggable={false}
          label={badgeLabel}
          size="small"
          color={badgeColor}
          sx={{ fontSize: "0.65rem", height: 20, textDecoration: "none" }}
        />
      )}
      footer={
        <Box
          component={Link}
          to={`/medium/${id}`}
          state={{ backgroundLocation: location }}
          tabIndex={selecting ? -1 : undefined}
          draggable={false}
          sx={{ display: "block", p: 0.75, bgcolor: "background.paper", color: "inherit", textDecoration: "none" }}
        >
          <Typography variant="caption" noWrap title={filename} sx={{ display: "block", fontSize: "0.7rem" }}>
            {filename}
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
            {caption}
          </Typography>
        </Box>
      }
    >
      <Box
        component="img"
        src={thumbUrl}
        alt={filename}
        loading="lazy"
        sx={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
        onError={(e) => { e.currentTarget.style.opacity = "0.3"; }}
      />
    </SelectableTileFrame>
  );
};

export default React.memo(SelectableMediaTile);
