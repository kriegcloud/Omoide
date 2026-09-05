import React, { useState } from "react";
import {
  Box,
  Typography,
  Avatar,
  Stack,
  Button,
  Paper,
  useTheme,
  CircularProgress,
  Chip,
  Menu,
  MenuItem,
} from "@mui/material";
import VisibilityOffIcon from "@mui/icons-material/VisibilityOff";
import DatasetIcon from "@mui/icons-material/Dataset";
import { alpha } from "@mui/material/styles";
import Grid from "@mui/material/Grid";
import { Person } from "../types";
import { PersonEditForm } from "./PersonEditForm";
import config, { API } from "../config";
import { encodeFilePath } from "../urlUtils";
import { PersonSocialLinks } from "./PersonSocialLinks";
import { useNavigate } from "react-router-dom";
import { createDatasetFromPerson } from "../services/datasets";

interface PersonHeroProps {
  person: Person;
  onSave: (formData: { name: string }) => Promise<void>;
  onGenderChange: (gender: "female" | "male" | null) => Promise<void>;
  onMerge: () => void;
  onDelete: () => void;
  onRefreshSimilar: () => void;
  onAutoSelectProfile: () => void;
  onHideToggle: () => void;
  saving: boolean;
  autoSelectingProfile: boolean;
}

export function PersonHero({
  person,
  onSave,
  onGenderChange,
  onMerge,
  onDelete,
  onRefreshSimilar,
  onAutoSelectProfile,
  onHideToggle,
  saving,
  autoSelectingProfile,
}: PersonHeroProps) {
  const theme = useTheme();
  const navigate = useNavigate();
  const [creatingDataset, setCreatingDataset] = useState(false);
  const [genderAnchor, setGenderAnchor] = useState<HTMLElement | null>(null);
  const thumbUrl = person.profile_face?.thumbnail_path
    ? `${API}/thumbnails/${encodeFilePath(
        person.profile_face.thumbnail_path
      )}`
    : undefined;

  return (
    <Box sx={{ mb: 4 }}>
      {/* CORRECTED: Using the standard Grid component with your project's 'size' prop syntax */}
      <Grid container spacing={{ xs: 2, md: 4 }} alignItems="center">
        {/* Profile Avatar */}
        <Grid size={{ xs: 12, sm: 4, md: 3 }} sx={{ textAlign: "center" }}>
          <Avatar
            src={thumbUrl}
            sx={{
              width: { xs: 120, md: 160 },
              height: { xs: 120, md: 160 },
              mx: "auto",
              border: `4px solid ${theme.palette.background.paper}`,
              boxShadow: theme.shadows[6],
            }}
          />
        </Grid>

        {/* Person Details and Actions */}
        <Grid size={{ xs: 12, sm: 8, md: 9 }}>
          <Stack direction="row" spacing={1.5} alignItems="center">
            <Typography variant="h3" component="h1" fontWeight="bold">
              {person.name || "Unnamed Person"}
            </Typography>
            {person.hidden_at && <Chip label="Hidden" color="warning" />}
            {(person.gender || !config.PRESENTATION_MODE) && (
              <Chip
                label={
                  !person.gender
                    ? "Set gender"
                    : person.gender_manual
                    ? `${person.gender === "female" ? "Female" : "Male"} (manual)`
                    : `${person.gender === "female" ? "Female" : "Male"}${
                        person.gender_confidence != null
                          ? ` · ${Math.round(person.gender_confidence * 100)}%`
                          : ""
                      }`
                }
                onClick={
                  config.PRESENTATION_MODE
                    ? undefined
                    : (event) => setGenderAnchor(event.currentTarget)
                }
                clickable={!config.PRESENTATION_MODE}
              />
            )}
          </Stack>
          {person.age != null && (
            <Typography variant="caption" color="text.secondary" display="block">
              ≈ {person.age}
            </Typography>
          )}
          <Menu
            anchorEl={genderAnchor}
            open={Boolean(genderAnchor)}
            onClose={() => setGenderAnchor(null)}
          >
            <MenuItem
              onClick={() => {
                setGenderAnchor(null);
                void onGenderChange("female");
              }}
            >
              Female
            </MenuItem>
            <MenuItem
              onClick={() => {
                setGenderAnchor(null);
                void onGenderChange("male");
              }}
            >
              Male
            </MenuItem>
            <MenuItem
              onClick={() => {
                setGenderAnchor(null);
                void onGenderChange(null);
              }}
            >
              Clear override
            </MenuItem>
          </Menu>
          <Typography variant="body1" color="text.secondary" gutterBottom>
            {person.appearance_count
              ? `${person.appearance_count} appearances found`
              : "No appearances"}
          </Typography>
          <PersonSocialLinks
            personId={person.id}
            initialLinks={person.social_links ?? []}
          />

          {!config.PRESENTATION_MODE && (
            <Stack
              direction="row"
              spacing={1}
              mt={2}
              mb={3}
              flexWrap="wrap"
              useFlexGap
            >
              <Button variant="outlined" onClick={onMerge} disabled={saving}>
                Merge
              </Button>
              <Button
                variant="outlined"
                onClick={() => onRefreshSimilar()}
                disabled={saving}
              >
                Refresh Similar
              </Button>
              <Button
                variant="outlined"
                onClick={onAutoSelectProfile}
                disabled={saving || autoSelectingProfile}
              >
                {autoSelectingProfile ? (
                  <CircularProgress size={18} thickness={5} />
                ) : (
                  "Auto Profile"
                )}
              </Button>
              <Button
                variant="outlined"
                startIcon={<DatasetIcon />}
                disabled={saving || creatingDataset}
                onClick={async () => {
                  setCreatingDataset(true);
                  try {
                    const dataset = await createDatasetFromPerson(person.id);
                    navigate(`/dataset/${dataset.id}`);
                  } finally {
                    setCreatingDataset(false);
                  }
                }}
              >
                {creatingDataset ? "Creating…" : "Create dataset"}
              </Button>
              <Button
                variant="outlined"
                color="warning"
                startIcon={<VisibilityOffIcon />}
                onClick={onHideToggle}
                disabled={saving}
              >
                {person.hidden_at ? "Unhide" : "Hide"}
              </Button>
              <Button
                variant="outlined"
                color="error"
                onClick={onDelete}
                disabled={saving}
              >
                Delete
              </Button>
            </Stack>
          )}
        </Grid>
      </Grid>

      {/* The Edit Form is now more cleanly integrated */}
      {!config.PRESENTATION_MODE && (
        <Paper
          sx={{
            p: { xs: 2, md: 3 },
            bgcolor: (theme) =>
              theme.palette.mode === 'dark'
                ? alpha(theme.palette.common.white, 0.05)
                : alpha(theme.palette.common.black, 0.02),
            mt: 4,
            borderRadius: 3,
          }}
        >
          <PersonEditForm
            initialPersonData={{ name: person.name ?? "" }}
            onSave={onSave}
            saving={saving}
          />
        </Paper>
      )}
    </Box>
  );
}
