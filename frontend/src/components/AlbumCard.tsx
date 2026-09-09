import { Box, Typography } from "@mui/material";
import PhotoAlbumIcon from "@mui/icons-material/PhotoAlbum";
import { API } from "../config";
import type { SelectionClickEvent } from "../hooks/useMarqueeSelection";
import type { Album } from "../types";
import { encodeFilePath } from "../urlUtils";
import SelectableTileFrame from "./SelectableTileFrame";

interface AlbumCardProps {
  album: Album;
  selecting: boolean;
  selected: boolean;
  onSelectionClick: (albumId: number, event: SelectionClickEvent) => boolean;
}

export default function AlbumCard({
  album,
  selecting,
  selected,
  onSelectionClick,
}: AlbumCardProps) {
  return (
    <SelectableTileFrame
      id={album.id}
      href={`/album/${album.id}`}
      aspectRatio="4/3"
      selecting={selecting}
      selected={selected}
      onSelectionClick={onSelectionClick}
      linkFooter
      footer={
        <Box sx={{ px: 2, py: 1.5 }}>
          <Typography variant="subtitle2" fontWeight={700} noWrap>
            {album.name}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {album.media_count} item{album.media_count === 1 ? "" : "s"}
          </Typography>
        </Box>
      }
    >
      <Box
        sx={{
          height: "100%",
          bgcolor: "action.hover",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          overflow: "hidden",
        }}
      >
        {album.cover_thumbnail ? (
          <Box
            component="img"
            src={`${API}/thumbnails/${encodeFilePath(album.cover_thumbnail)}`}
            alt={album.name}
            loading="lazy"
            draggable={false}
            sx={{ width: "100%", height: "100%", objectFit: "cover" }}
          />
        ) : (
          <PhotoAlbumIcon color="disabled" sx={{ fontSize: 48 }} />
        )}
      </Box>
    </SelectableTileFrame>
  );
}
