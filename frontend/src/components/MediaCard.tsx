import React, { lazy, Suspense, useState, useRef, useEffect } from "react";
import { useLocation } from "react-router-dom";
import {
  CircularProgress,
  CardMedia,
  Box,
  Chip,
  Typography,
} from "@mui/material";
import { MediaPreview } from "../types";
import appConfig, { API } from "../config";
import { encodeFilePath } from "../urlUtils";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import PlayCircleOutlineIcon from "@mui/icons-material/PlayCircleOutline";
import FavoriteIcon from "@mui/icons-material/Favorite";
import { useSelection } from "../context/SelectionContext";
import MediaCardMenu, { MediaPersonContext } from "./MediaCardMenu";
import DatasetItemMenu from "./DatasetItemMenu";
import SelectableTileFrame from "./SelectableTileFrame";
import type { SelectionClickEvent } from "../hooks/useMarqueeSelection";

const ReactPlayer = lazy(() => import("react-player"));

export interface MediaDatasetContext {
  caption?: string | null;
  excluded: boolean;
  excludedReason?: "duplicate" | "burst" | "manual" | "quality" | null;
  hasOps: boolean;
  detScore?: number | null;
  frontality?: number | null;
  faceCount: number;
  framing?: string;
  sharpness?: number | null;
  otherPeople?: number;
  identityDistance?: number | null;
  onToggleExcluded: () => void;
  onEditCaption: () => void;
  onEditCrop: () => void;
  onRemove: () => void;
}

function formatDuration(d?: number): string {
  if (d == null) return "";
  const m = Math.floor(d / 60);
  const s = Math.round(d % 60)
    .toString()
    .padStart(2, "0");
  return `${m}:${s}`;
}

const WINDOWS_ABS_RE = /^[a-zA-Z]:[\\/]/;

function isAbsolutePath(value: string): boolean {
  return value.startsWith("/") || WINDOWS_ABS_RE.test(value);
}

function toFileUri(value: string): string | null {
  if (!value) return null;
  const normalized = value.replace(/\\/g, "/");
  if (!isAbsolutePath(normalized)) return null;
  if (WINDOWS_ABS_RE.test(normalized)) {
    return `file:///${encodeURI(normalized)}`;
  }
  return `file://${encodeURI(normalized)}`;
}

function guessMimeType(filename: string): string {
  const ext = filename.split(".").pop()?.toLowerCase();
  switch (ext) {
    case "jpg":
    case "jpeg":
      return "image/jpeg";
    case "png":
      return "image/png";
    case "gif":
      return "image/gif";
    case "bmp":
      return "image/bmp";
    case "webp":
      return "image/webp";
    case "tif":
    case "tiff":
      return "image/tiff";
    default:
      return "application/octet-stream";
  }
}

interface MediaNavigationContext {
  ids: number[];
}

interface MediaCardProps {
  media: MediaPreview;
  mediaListKey?: string;
  navigationContext?: MediaNavigationContext;
  personContext?: MediaPersonContext;
  datasetContext?: MediaDatasetContext;
  onSelectionClick?: (id: number, event: SelectionClickEvent) => boolean;
}

export default function MediaCard({
  media,
  mediaListKey,
  navigationContext,
  personContext,
  datasetContext,
  onSelectionClick,
}: MediaCardProps) {
  const { isSelecting, selectedIds, toggle, beginSelecting } = useSelection();
  // This state now explicitly controls when the video player is active.
  const [isPlayerActive, setIsPlayerActive] = useState(false);
  const [playerUrl, setPlayerUrl] = useState<string | null>(null);
  const hoverTimeoutRef = useRef<number | null>(null);
  const hasInitializedPlayerRef = useRef(false);
  const location = useLocation();
  const [isFavorite, setIsFavorite] = useState(!!media?.is_favorite);
  const memeModeEnabled = appConfig.MEME_MODE;
  const isGif =
    media != null
      ? media.filename.toLowerCase().endsWith(".gif") ||
        media.path.toLowerCase().endsWith(".gif")
      : false;
  const useOriginalGif = memeModeEnabled && isGif;

  const isVideo = media ? typeof media.duration === "number" && !isGif : false;
  const isDraggable = !!media && !isVideo && !isSelecting;

  const mediaUrl = media
    ? `${API}/originals/${encodeFilePath(media.path)}${media.cache_version ? `?v=${media.cache_version}` : ""}`
    : `${API}/static/brand/404.png`;
  const filename = media ? media.filename : "404 Not found";
  const mediaId = media ? media.id : null;
  const isSelected = mediaId != null && selectedIds.has(mediaId);
  let thumbUrl;
  if (media) {
    if (useOriginalGif) {
      thumbUrl = mediaUrl;
    } else if (media.thumbnail_path) {
      thumbUrl = `${API}/thumbnails/${encodeFilePath(media.thumbnail_path)}${media.cache_version ? `?v=${media.cache_version}` : ""}`;
    } else {
      thumbUrl = `${API}/thumbnails/${media.id}.jpg`;
    }
  } else {
    thumbUrl = `${API}/static/brand/404.png`;
  }
  const linkState = {
    backgroundLocation: location.state?.backgroundLocation || location,
    mediaListKey,
    media,
    navigationContext,
  };

  useEffect(() => {
    return () => {
      if (hoverTimeoutRef.current) {
        clearTimeout(hoverTimeoutRef.current);
      }
    };
  }, []);

  useEffect(() => {
    // Reset player state when card content changes (e.g., reused component).
    setIsPlayerActive(false);
    setPlayerUrl(null);
    hasInitializedPlayerRef.current = false;
  }, [mediaId, mediaUrl]);

  useEffect(() => {
    setIsFavorite(!!media?.is_favorite);
  }, [mediaId, media?.is_favorite]);

  const handleMouseEnter = () => {
    hoverTimeoutRef.current = window.setTimeout(() => {
      if (!hasInitializedPlayerRef.current) {
        hasInitializedPlayerRef.current = true;
        setPlayerUrl(mediaUrl);
      }
      setIsPlayerActive(true);
    }, 200);
  };

  const handleMouseLeave = () => {
    if (hoverTimeoutRef.current) {
      clearTimeout(hoverTimeoutRef.current);
    }
    setIsPlayerActive(false);
    setPlayerUrl(null);
    hasInitializedPlayerRef.current = false;
  };

  const handleDragStart = (event: React.DragEvent<HTMLElement>) => {
    if (!media || isVideo) return;
    const filePath = media.path;
    const fileUri = toFileUri(filePath);
    const fallbackUri = fileUri ?? mediaUrl;
    const downloadUrl = mediaUrl;

    event.dataTransfer.effectAllowed = "copy";
    const uriList = [downloadUrl, fileUri].filter(Boolean).join("\r\n");
    event.dataTransfer.setData("text/uri-list", uriList || downloadUrl);
    event.dataTransfer.setData(
      "text/plain",
      isAbsolutePath(filePath) ? filePath : fallbackUri
    );
    event.dataTransfer.setData(
      "DownloadURL",
      `${guessMimeType(media.filename)}:${media.filename}:${downloadUrl}`
    );
    event.dataTransfer.setData("text/html", `<img src="${mediaUrl}">`);
    event.stopPropagation();
  };

  const handleSelectionClick = (id: number, event: SelectionClickEvent) => {
    if (onSelectionClick) return onSelectionClick(id, event);
    if (isSelecting || event.ctrlKey || event.metaKey || event.shiftKey) {
      if (!isSelecting) beginSelecting();
      toggle(id);
      return true;
    }
    return false;
  };

  return (
    <SelectableTileFrame
      id={mediaId ?? 0}
      data-selectable-id={mediaId ?? undefined}
      data-media-card
      selected={isSelected}
      selecting={isSelecting}
      onSelectionClick={handleSelectionClick}
      href={`/medium/${mediaId}`}
      linkState={linkState}
      replace={!!location.state?.backgroundLocation}
      sx={{
        opacity: datasetContext?.excluded ? 0.48 : 1,
        "&:hover .media-overlay": { opacity: 1 },
      }}
      menu={media && (datasetContext ? (
        <DatasetItemMenu context={datasetContext} />
      ) : (
        <MediaCardMenu
          media={media}
          mediaListKey={mediaListKey}
          personContext={personContext}
          onMediaChange={(updated) => setIsFavorite(updated.is_favorite)}
        />
      ))}
      topLeft={media && isFavorite && (
        <FavoriteIcon
          aria-label="Favorite"
          fontSize="small"
          sx={{ color: "error.main", filter: "drop-shadow(0 1px 2px rgba(0,0,0,0.65))" }}
        />
      )}
      footer={datasetContext && (
        <Box sx={{ px: 1.25, py: 1, minHeight: 66 }}>
          <Typography variant="caption" color="text.secondary" noWrap display="block">
            {datasetContext.caption || "No caption"}
          </Typography>
          <Box sx={{ display: "flex", gap: 0.5, mt: 0.75, flexWrap: "wrap" }}>
            {datasetContext.detScore != null && <Chip size="small" label={`Face ${Math.round(datasetContext.detScore * 100)}%`} />}
            {datasetContext.frontality != null && <Chip size="small" label={`Front ${Math.round(datasetContext.frontality * 100)}%`} />}
            {datasetContext.framing && <Chip size="small" label={datasetContext.framing.replace("_", " ")} />}
            {datasetContext.sharpness != null && <Chip size="small" label={`Sharp ${Math.round(datasetContext.sharpness)}`} />}
            {(datasetContext.otherPeople ?? 0) > 0 && <Chip size="small" color="warning" label={`+${datasetContext.otherPeople} people`} />}
            {datasetContext.identityDistance != null && <Chip size="small" label={`ID ${datasetContext.identityDistance.toFixed(2)}`} />}
            {datasetContext.faceCount > 1 && <Chip size="small" color="warning" label={`${datasetContext.faceCount} faces`} />}
            {datasetContext.hasOps && <Chip size="small" color="primary" label="Cropped" />}
            {datasetContext.excluded && <Chip size="small" color="error" label={datasetContext.excludedReason ? `Excluded: ${datasetContext.excludedReason}` : "Excluded"} />}
          </Box>
        </Box>
      )}
    >
          <Box className="media-preview"
            draggable={isDraggable}
            onDragStart={isDraggable ? handleDragStart : undefined}
            onMouseEnter={isVideo ? handleMouseEnter : undefined}
            onMouseLeave={isVideo ? handleMouseLeave : undefined}
            sx={{
              position: "relative",
              display: "block",
              width: "100%",
              paddingTop: "100%", // 1:1 Aspect Ratio
              cursor: isDraggable ? "grab" : "pointer",
            }}
          >
          <Box
            sx={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              height: "100%",
              bgcolor: "action.hover", // Placeholder color
            }}
          >
            {/* We now explicitly render the thumbnail image for videos */}
            <CardMedia
              component="img"
              draggable={false}
              src={thumbUrl}
              alt={filename}
              width={media?.width || undefined}
              height={media?.height || undefined}
              sx={{
                width: "100%",
                height: "100%",
                objectFit: "cover",
                // The thumbnail is visible when the player is NOT active
                opacity: isPlayerActive ? 0 : 1,
                transition: "opacity 0.3s ease-in-out",
              }}
            />

            {/* The player is only mounted while hovering a video card */}
            {isVideo && playerUrl && (
              <Box
                sx={{
                  width: "100%",
                  height: "100%",
                  // The player is visible ONLY when it's active
                  opacity: isPlayerActive ? 1 : 0,
                  transition: "opacity 0.3s ease-in-out",
                  // Keep pointer events to allow ReactPlayer to be interactive
                }}
              >
                <Suspense fallback={<CircularProgress size={24} sx={{ position: "absolute", top: "calc(50% - 12px)", left: "calc(50% - 12px)" }} />}>
                  <ReactPlayer
                    url={playerUrl}
                    playing={isPlayerActive}
                    loop
                    muted
                    width="100%"
                    height="100%"
                    playsinline
                    config={{
                      file: {
                        attributes: {
                          crossOrigin: "anonymous",
                          preload: "metadata",
                        },
                      },
                    }}
                    style={{ position: "absolute", top: 0, left: 0 }}
                  />
                </Suspense>
              </Box>
            )}
          </Box>

          {/* Play Icon Overlay */}
          {isVideo && (
            <Box
              sx={{
                position: "absolute",
                top: "50%",
                left: "50%",
                transform: "translate(-50%, -50%) scale(0.8)",
                transition: "all 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
                opacity: isPlayerActive ? 0 : 0.6,
                pointerEvents: "none",
                bgcolor: "rgba(0,0,0,0.3)",
                borderRadius: "50%",
                p: 1,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                backdropFilter: "blur(4px)",
                ".media-preview:hover &": {
                    transform: "translate(-50%, -50%) scale(1)",
                    opacity: isPlayerActive ? 0 : 1,
                    bgcolor: "rgba(0,0,0,0.5)",
                }
              }}
            >
              <PlayArrowIcon
                sx={{
                  fontSize: "2.5rem",
                  color: "common.white",
                }}
              />
            </Box>
          )}

          {/* Info Overlay */}
          <Box
            className="media-overlay"
            sx={{
              position: "absolute",
              bottom: 0,
              left: 0,
              width: "100%",
              p: 2,
              background: "linear-gradient(to top, rgba(0,0,0,0.8) 0%, rgba(0,0,0,0.4) 60%, rgba(0,0,0,0) 100%)",
              pointerEvents: "none",
              opacity: 0.8, // Always slightly visible
              transition: "opacity 0.3s ease-in-out",
            }}
          >
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "flex-end",
              }}
            >
              {isVideo && media ? (
                <Box 
                    display="flex" 
                    alignItems="center" 
                    gap={0.5} 
                    sx={{ 
                        bgcolor: "rgba(0,0,0,0.6)", 
                        borderRadius: 1, 
                        px: 0.8, 
                        py: 0.2,
                        backdropFilter: "blur(4px)"
                    }}
                >
                  <PlayCircleOutlineIcon sx={{ fontSize: "0.9rem", color: "common.white" }} />
                  <Typography variant="caption" sx={{ color: "common.white", fontWeight: 600, letterSpacing: 0.5 }}>
                    {formatDuration(media.duration)}
                  </Typography>
                </Box>
              ) : (
                <div />
              )}
              
              {(media?.width && media?.height) && (
                  <Typography 
                    variant="caption" 
                    sx={{ 
                        color: "rgba(255,255,255,0.9)", 
                        textShadow: "0 1px 2px rgba(0,0,0,0.5)",
                        fontFamily: "monospace",
                        fontSize: "0.7rem"
                    }}
                  >
                    {media.width}×{media.height}
                  </Typography>
              )}
            </Box>
          </Box>
        </Box>

    </SelectableTileFrame>
  );
}
