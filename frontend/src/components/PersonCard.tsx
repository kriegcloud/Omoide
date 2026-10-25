import { useEffect, useState } from "react";
import { IconButton, Tooltip } from "@mui/material";
import FavoriteIcon from "@mui/icons-material/Favorite";
import FavoriteBorderIcon from "@mui/icons-material/FavoriteBorder";
import config from "../config";
import { setPersonFavorite } from "../services/personActions";
import { Box, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import type { PersonReadSimple } from "../types";
import { API } from "../config";
import { encodeFilePath } from "../urlUtils";
import type { SelectionClickEvent } from "../hooks/useMarqueeSelection";
import SelectableTileFrame from "./SelectableTileFrame";

type PersonCardPerson = Pick<PersonReadSimple, "id" | "name"> & { profile_face?: { thumbnail_path?: string | null } | null; appearance_count?: number; is_favorite?: boolean };

interface PersonCardProps {
  person: PersonCardPerson;
  selecting?: boolean;
  selected?: boolean;
  onSelectionClick?: (personId: number, event: SelectionClickEvent) => boolean;
}

const getInitials = (name = "") => {
  const parts = name.split(" ");
  if (parts.length > 1) {
    return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase();
  }
  return name.substring(0, 2).toUpperCase();
};

export default function PersonCard({
  person,
  selecting = false,
  selected = false,
  onSelectionClick,
}: PersonCardProps) {
  const [favorite, setFavorite] = useState(Boolean(person.is_favorite));
  const [favoriteBusy, setFavoriteBusy] = useState(false);
  useEffect(() => setFavorite(Boolean(person.is_favorite)), [person.id, person.is_favorite]);
  const toggleFavorite = async () => {
    if (favoriteBusy) return;
    const next = !favorite; setFavorite(next); setFavoriteBusy(true);
    try { await setPersonFavorite(person.id, next); } catch { setFavorite(!next); } finally {setFavoriteBusy(false);}
  };
  const fallbackName = person.name || "Unknown";

  const thumbUrl = person.profile_face?.thumbnail_path
    ? `${API}/thumbnails/${encodeFilePath(
        person.profile_face.thumbnail_path
      )}`
    : undefined;

  const appearanceLabel =
    typeof person.appearance_count === "number" && person.appearance_count > 0
      ? `${person.appearance_count} media`
      : "";

  return (
    <SelectableTileFrame
      id={person.id}
      href={`/person/${person.id}`}
      aspectRatio="3/4"
      selecting={selecting}
      selected={selected}
      onSelectionClick={onSelectionClick}
      sx={{
        color: "common.white",
        background: thumbUrl
          ? `url("${thumbUrl}")`
          : (themeParam) =>
              `linear-gradient(135deg, ${themeParam.palette.primary.main}, ${themeParam.palette.primary.dark})`,
        backgroundSize: "cover",
        backgroundPosition: "center",
      }}
    >
      {!selecting && (config.PRESENTATION_MODE ? favorite && <FavoriteIcon aria-label="Favorite" color="error" sx={{position: "absolute", top: 8, right: 48, zIndex: 25}} /> :
        <Tooltip title={favorite ? "Remove from favorites" : "Add to favorites"}><IconButton data-tile-control aria-label={favorite ? "Remove from favorites" : "Add to favorites"} aria-pressed={favorite} disabled={favoriteBusy} onClick={event => {event.preventDefault(); event.stopPropagation(); void toggleFavorite();}} sx={{position: "absolute", top: 8, right: 48, zIndex: 25, bgcolor: "rgba(0,0,0,.45)", color: favorite ? "error.main" : "common.white"}}>{favorite ? <FavoriteIcon /> : <FavoriteBorderIcon />}</IconButton></Tooltip>)}
      {!thumbUrl && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            height: "100%",
          }}
        >
          <Typography variant="h4" fontWeight="bold">
            {getInitials(fallbackName)}
          </Typography>
        </Box>
      )}

      <Box
        sx={{
          position: "absolute",
          top: 0,
          left: 0,
          width: "100%",
          height: "100%",
          background: (themeParam) =>
            `linear-gradient(to top, ${alpha(themeParam.palette.common.black, themeParam.palette.mode === "dark" ? 0.8 : 0.6)} 0%, ${alpha(themeParam.palette.common.black, 0)} 50%)`,
          display: "flex",
          flexDirection: "column",
          justifyContent: "flex-end",
          p: 1.5,
        }}
      >
        <Typography variant="subtitle1" fontWeight="bold" lineHeight={1.2}>
          {fallbackName}
        </Typography>
        <Typography
          variant="caption"
          sx={{
            color: (themeParam) =>
              alpha(
                themeParam.palette.common.white,
                themeParam.palette.mode === "dark" ? 0.7 : 0.85
              ),
            mt: 0.5,
          }}
        >
          {appearanceLabel}
        </Typography>
      </Box>
    </SelectableTileFrame>
  );
}
