// components/DuplicateMediaCard.tsx

import React from "react";
import { useLocation } from "react-router-dom";
import {
  CardMedia,
  CardContent,
  Typography,
  Radio,
  FormControlLabel,
  useTheme,
} from "@mui/material";
import { MediaDuplicate } from "../types";
import { API } from "../config";
import { encodeFilePath } from "../urlUtils";
import { formatBytes } from "../formatUtils";
import SelectableTileFrame from "./SelectableTileFrame";
import MediaCardMenu from "./MediaCardMenu";
import type { SelectionClickEvent } from "../hooks/useMarqueeSelection";

interface DuplicateMediaCardProps {
  media: MediaDuplicate;
  groupId: number;
  isSelectedAsMaster: boolean;
  onSelectMaster: () => void;
  selecting: boolean;
  selected: boolean;
  onSelectionClick: (id: number, event: SelectionClickEvent) => boolean;
}

export const DuplicateMediaCard: React.FC<DuplicateMediaCardProps> = ({
  media,
  groupId,
  isSelectedAsMaster,
  onSelectMaster,
  selecting,
  selected,
  onSelectionClick,
}) => {
  const location = useLocation();
  const theme = useTheme();

  const filename = media ? media.filename : "404 Not found";
  const thumbUrl = media.thumbnail_path
    ? `${API}/thumbnails/${encodeFilePath(media.thumbnail_path)}`
    : `${API}/thumbnails/${media.id}.jpg`;

  return (
    media && (
      <SelectableTileFrame
        id={media.id}
        selected={selected}
        selecting={selecting}
        onSelectionClick={onSelectionClick}
        href={`/medium/${media.id}`}
        linkState={{ backgroundLocation: location }}
        aspectRatio="4 / 3"
        menu={<MediaCardMenu media={media} />}
        sx={{
          height: "100%",
          border: isSelectedAsMaster
            ? `2px solid ${theme.palette.primary.main}`
            : `2px solid transparent`,
          boxShadow: isSelectedAsMaster ? theme.shadows[4] : theme.shadows[1],
        }}
        footer={
          <CardContent>
            <FormControlLabel
              data-tile-control
              data-no-marquee
              control={
                <Radio
                  checked={isSelectedAsMaster}
                  onChange={onSelectMaster}
                  name={`master-select-${groupId}`}
                />
              }
              label="Keep this one"
            />
            <Typography
              variant="body2"
              color="text.secondary"
              noWrap
              title={media.path}
            >
              {media.path}
            </Typography>
            {/* Displaying more metadata helps the user choose */}
            <Typography variant="caption" color="text.secondary" display="block">
              {media.width}x{media.height}
            </Typography>
            <Typography variant="caption" color="text.secondary" display="block">
              {formatBytes(media.size)}
            </Typography>
          </CardContent>
        }
      >
        <CardMedia
          component="img"
          image={thumbUrl}
          alt={filename}
          draggable={!selecting}
          sx={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
      </SelectableTileFrame>
    )
  );
};
