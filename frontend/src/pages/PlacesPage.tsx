import GalleryPage from "../components/GalleryPage";
import Gallery from "../components/Gallery";
import { useMutationRefresh } from "../context/UndoContext";
import React, { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardActionArea,
  CardContent,
  CircularProgress,
  Snackbar,
  Typography,
} from "@mui/material";
import PublicIcon from "@mui/icons-material/Public";
import TravelExploreIcon from "@mui/icons-material/TravelExplore";
import { Link } from "react-router-dom";
import config, { API } from "../config";
import { encodeFilePath } from "../urlUtils";
import { EmptyState } from "../components/EmptyState";
import { getPlaces, startGeocodePlaces } from "../services/features";
import { useTaskCompletionVersion } from "../TaskEventsContext";
import { PlaceCountry } from "../types";

export default function PlacesPage() {
  const [places, setPlaces] = useState<PlaceCountry[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [snackbar, setSnackbar] = useState("");
  const refreshKey = useTaskCompletionVersion(["geocode_places"]);

  const [revision, setRevision] = useState(0);
  useMutationRefresh(["media:deleted", "media:updated", "media:moved", "list:invalidate"], () => setRevision(value => value + 1));

  useEffect(() => {
    let active = true;
    setError(null);
    setIsLoading(true);
    getPlaces()
      .then(data => { if (active) setPlaces(data); })
      .catch((err) =>
        active && setError(err instanceof Error ? err.message : "Failed to load places")
      )
      .finally(() => { if (active) setIsLoading(false); });
    return () => { active = false; };
  }, [refreshKey, revision]);

  const handleGeocode = async () => {
    try {
      await startGeocodePlaces();
      setSnackbar("Geocoding started — this page refreshes when done.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start task");
    }
  };

  return (
    <GalleryPage title="Places" count={places.length}>
      <Box
        display="flex"
        justifyContent="space-between"
        alignItems="center"
        flexWrap="wrap"
        gap={1}
        mb={3}
      >
        <Box display="flex" alignItems="center" gap={1}>
          <PublicIcon color="primary" />

        </Box>
        {!config.PRESENTATION_MODE && (
          <Button
            variant="outlined"
            startIcon={<TravelExploreIcon />}
            onClick={handleGeocode}
          >
            Geocode new media
          </Button>
        )}
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {isLoading ? (
        <Box textAlign="center" py={6}>
          <CircularProgress />
        </Box>
      ) : places.length === 0 ? (
        <EmptyState
          icon={<PublicIcon />}
          title="No places yet"
          description={
            config.PRESENTATION_MODE
              ? undefined
              : "Run “Geocode new media” to turn GPS data into browsable cities and countries (offline)."
          }
        />
      ) : (
        places.map((country) => (
          <Box key={country.country} mb={4}>
            <Typography variant="subtitle1" fontWeight={700} mb={1.5}>
              {country.country} · {country.count.toLocaleString()} items
            </Typography>
            <Box><Gallery>
              {country.cities.map((city) => (
                <Card key={city.city} sx={{ borderRadius: 1 }}>
                  <CardActionArea
                    component={Link}
                    to={`/places/media?city=${encodeURIComponent(
                      city.city
                    )}&country=${encodeURIComponent(country.country)}`}
                  >
                    <Box
                      sx={{
                        aspectRatio: "1/1",
                        bgcolor: "action.hover",
                        overflow: "hidden",
                      }}
                    >
                      {city.cover_thumbnail && (
                        <Box
                          component="img"
                          src={`${API}/thumbnails/${encodeFilePath(
                            city.cover_thumbnail
                          )}`}
                          alt={city.city}
                          loading="lazy"
                          sx={{
                            width: "100%",
                            height: "100%",
                            objectFit: "cover",
                          }}
                        />
                      )}
                    </Box>
                    <CardContent sx={{ py: 1 }}>
                      <Typography variant="subtitle2" fontWeight={700} noWrap>
                        {city.city}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {city.count.toLocaleString()} item
                        {city.count === 1 ? "" : "s"}
                      </Typography>
                    </CardContent>
                  </CardActionArea>
                </Card>
              ))}
            </Gallery></Box>
          </Box>
        ))
      )}

      <Snackbar
        open={!!snackbar}
        autoHideDuration={4000}
        onClose={() => setSnackbar("")}
        message={snackbar}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      />
    </GalleryPage>
  );
}
