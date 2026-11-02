import { useSyncExternalStore } from "react";
import { IconButton, Tooltip } from "@mui/material";
import FavoriteIcon from "@mui/icons-material/Favorite";
import FavoriteBorderIcon from "@mui/icons-material/FavoriteBorder";
import config from "../config";
import { optimisticOperations, projectEntity } from "../stores/optimisticOperations";
import { startPeopleMutation, setPersonFavorite } from "../services/personActions";
import { Box, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import type { PersonReadSimple } from "../types";
import { useFaceThumbnail, type FaceThumbnailData } from "../hooks/useFaceThumbnail";
import FaceBoxOverlay from "./FaceBoxOverlay";
import type { SelectionClickEvent } from "../hooks/useMarqueeSelection";
import SelectableTileFrame from "./SelectableTileFrame";

type PersonCardPerson = Pick<PersonReadSimple, "id" | "name"> & { profile_face?: FaceThumbnailData | null; appearance_count?: number; is_favorite?: boolean };

interface PersonCardProps {
  person: PersonCardPerson;
  queryKey?: string;
  selecting?: boolean;
  selected?: boolean;
  onSelectionClick?: (personId: number, event: SelectionClickEvent) => boolean;
  secondaryLabel?: string;
  showFavorite?: boolean;
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
  queryKey = "detail",
  selecting = false,
  selected = false,
  onSelectionClick,
  secondaryLabel,
  showFavorite = true,
}: PersonCardProps) {
  useSyncExternalStore(optimisticOperations.subscribe, optimisticOperations.getSnapshot, optimisticOperations.getSnapshot);
  const projected = projectEntity(`person:${person.id}`, person, queryKey);
  const favorite = Boolean(projected?.is_favorite);
  const favoriteBusy = optimisticOperations.getSnapshot().some(record =>
    record.resources.includes(`person:${person.id}`) && ["applied", "pending", "unknown"].includes(record.state));
  const toggleFavorite = () => {
    if (favoriteBusy || config.PRESENTATION_MODE) return;
    const next = !favorite;
    startPeopleMutation("Update person favorite", [person.id], () => setPersonFavorite(person.id, next), {
      patches: [{ kind: "fields", key: `person:${person.id}`, values: { is_favorite: next } }],
    });
  };
  const fallbackName = person.name || "Unknown";

  const thumbnail = useFaceThumbnail(projected?.profile_face);
  const thumbUrl = thumbnail.src;

  const appearanceLabel =
    typeof person.appearance_count === "number" && person.appearance_count > 0
      ? `${person.appearance_count} media`
      : "";

  const captionLabel = secondaryLabel ?? appearanceLabel;

  return (
    <SelectableTileFrame
      id={person.id}
      href={`/person/${person.id}`}
      aspectRatio={thumbnail.aspectRatio}
      selecting={selecting}
      selected={selected}
      onSelectionClick={onSelectionClick}
      menu={selecting || !showFavorite ? undefined : config.PRESENTATION_MODE ? (
        favorite && (
          <FavoriteIcon aria-label="Favorite" color="error" sx={{ m: 0.5, display: "block" }} />
        )
      ) : (
        <Tooltip title={favorite ? "Remove from favorites" : "Add to favorites"}>
          <IconButton
            data-tile-control
            size="small"
            aria-label={favorite ? "Remove from favorites" : "Add to favorites"}
            aria-pressed={favorite}
            disabled={favoriteBusy}
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              void toggleFavorite();
            }}
            sx={{
              width: 32,
              height: 32,
              color: favorite ? "error.main" : "common.white",
              "&:hover": { bgcolor: "rgba(0,0,0,.25)" },
            }}
          >
            {favorite ? <FavoriteIcon fontSize="small" /> : <FavoriteBorderIcon fontSize="small" />}
          </IconButton>
        </Tooltip>
      )}
      sx={{
        color: "common.white",
        background: (themeParam) =>
          `linear-gradient(135deg, ${themeParam.palette.primary.main}, ${themeParam.palette.primary.dark})`,
      }}
    >
      {thumbUrl && (
        <Box component="img" src={thumbUrl} alt={thumbnail.showsParent ? `Photo containing ${fallbackName}` : `Face crop for ${fallbackName}`} loading="lazy" draggable={false} onLoad={thumbnail.onLoad} onError={thumbnail.onError} sx={{ display: "block", width: "100%", height: "100%", objectFit: "contain" }} />
      )}
      {thumbnail.showsParent && thumbnail.dimensions && (
        <FaceBoxOverlay bbox={person.profile_face?.bbox} sourceWidth={thumbnail.dimensions[0]} sourceHeight={thumbnail.dimensions[1]} space="detector" />
      )}
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
          p: 1,
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
          {captionLabel}
          {thumbUrl && !thumbnail.showsParent && `${captionLabel ? " · " : ""}Face crop`}
        </Typography>
      </Box>
    </SelectableTileFrame>
  );
}
