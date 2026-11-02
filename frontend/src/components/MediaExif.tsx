import { useMutationRefresh } from "../context/UndoContext";
import ListState from "./ListState";
import React, { useState, useEffect } from "react";
import {
  Box,
  Typography,
  Paper,
  Divider,
  Link as MuiLink,
} from "@mui/material";
import Grid from "@mui/material/Grid";
import { Link } from "react-router-dom";

// Icons for a nicer look
import CameraAltIcon from "@mui/icons-material/CameraAlt";
import PhotoCameraIcon from "@mui/icons-material/PhotoCamera";
import AccessTimeIcon from "@mui/icons-material/AccessTime";
import IsoIcon from "@mui/icons-material/Iso";
import ShutterSpeedIcon from "@mui/icons-material/ShutterSpeed";
import ApertureIcon from "@mui/icons-material/DonutLarge";
import MyLocationIcon from "@mui/icons-material/MyLocation";
import { getExifData } from "../services/exif";
import { ExifData } from "../types";

interface MediaExifProps {
  mediaId: number;
}

export function MediaExif({ mediaId }: MediaExifProps) {
  // State: 'loading', 'loaded', or 'error'
  const [status, setStatus] = useState<"loading" | "loaded" | "error">(
    "loading"
  );
  const [retry, setRetry] = useState(0);
  const [exif, setExif] = useState<ExifData | null>(null);

  useMutationRefresh(["media:updated", "media:moved", "media:deleted", "list:invalidate"], () => setRetry(value => value + 1));

  useEffect(() => {
    // This effect runs only when the component is first mounted (i.e., when the tab is clicked)
    let isCancelled = false;
    setStatus("loading");

    getExifData(mediaId)
      .then((body) => {
        if (!isCancelled) {
          setExif(body);
          setStatus("loaded");
        }
      })
      .catch(() => {
        if (!isCancelled) {
          setStatus("error");
          setExif(null);
        }
      });

    // Cleanup function to prevent state updates on unmounted components
    return () => {
      isCancelled = true;
    };
  }, [mediaId, retry]); // Re-fetches if the mediaId changes while the tab is open

  // Decide "has data" from displayable values, not object keys: a successful
  // extraction with every field null used to render a blank panel (R9-02).
  const present = (value: unknown) => value !== null && value !== undefined && value !== "";
  const camera = exif ? [exif.make, exif.model].map((part) => (typeof part === "string" ? part.trim() : part)).filter(Boolean).join(" ") : "";
  const fields: { key: string; icon: React.ReactNode; label: string; value: React.ReactNode }[] = exif ? [
    { key: "camera", icon: <CameraAltIcon />, label: "Camera", value: camera || null },
    // EXIF timestamps are the camera's local clock; keep them as local time.
    { key: "shot", icon: <AccessTimeIcon />, label: "Shot", value: present(exif.timestamp) ? new Date(exif.timestamp as string).toLocaleString() : null },
    { key: "aperture", icon: <ApertureIcon />, label: "Aperture", value: present(exif.aperture) ? exif.aperture : null },
    { key: "shutter", icon: <ShutterSpeedIcon />, label: "Shutter Speed", value: present(exif.exposure_time) ? `${exif.exposure_time}s` : null },
    { key: "iso", icon: <IsoIcon />, label: "ISO", value: present(exif.iso) ? exif.iso : null },
    { key: "focal", icon: <PhotoCameraIcon />, label: "Focal Length", value: present(exif.focal_length) ? `${exif.focal_length}mm` : null },
  ].filter((field) => field.value !== null) : [];
  const hasLocation = !!exif && exif.lat != null && exif.lon != null;

  if (status !== "loaded" || !exif || (fields.length === 0 && !hasLocation)) {
    return <ListState loading={status === "loading"} error={status === "error" ? "Failed to load EXIF data." : null} empty={status === "loaded"} emptyMessage="No EXIF data available." onRetry={() => setRetry(value => value + 1)} />;
  }

  return (
    <Paper variant="outlined" sx={{ p: 3, backgroundColor: "action.hover" }}>
      {fields.length > 0 && (
        <Grid container spacing={3}>
          {fields.map((field) => (
            <Grid key={field.key} size={{ xs: 12, sm: 6, md: 4 }} sx={{ display: "flex", alignItems: "center", gap: 2 }}>
              <Box sx={{ color: "text.secondary" }}>{field.icon}</Box>
              <Box>
                <Typography variant="body2" color="text.secondary">{field.label}</Typography>
                <Typography variant="body1" fontWeight="500">{field.value}</Typography>
              </Box>
            </Grid>
          ))}
        </Grid>
      )}
      {hasLocation && (
        <>
          {fields.length > 0 && <Divider sx={{ my: 3 }} />}
          <MuiLink
            component={Link}
            to={`/map?lat=${exif?.lat}&lng=${exif?.lon}`}
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1,
              textDecoration: "none",
              color: "primary.main",
              fontWeight: 500,
            }}
          >
            <MyLocationIcon /> View on map
          </MuiLink>
        </>
      )}
    </Paper>
  );
}
