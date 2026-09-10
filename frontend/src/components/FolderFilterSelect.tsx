import { useEffect, useId, useState } from "react";
import {
  Box,
  Button,
  FormControl,
  FormHelperText,
  InputLabel,
  MenuItem,
  Select,
} from "@mui/material";
import { getMediaFolders } from "../services/media";
import type { MediaFolderListing } from "../types";

interface FolderFilterSelectProps {
  value: string | null;
  onChange: (folder: string | null) => void;
}

/** Compact navigation through the same folder listings used by Images. */
export default function FolderFilterSelect({ value, onChange }: FolderFilterSelectProps) {
  const labelId = useId();
  const [listing, setListing] = useState<MediaFolderListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let active = true;
    setListing(null);
    setError(null);
    setLoading(true);
    getMediaFolders(value, 0)
      .then((result) => {
        if (active) setListing(result);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "Failed to load folders");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [value, retry]);

  const options = new Map<string, string>();
  for (const crumb of listing?.breadcrumbs ?? []) {
    if (crumb.path) options.set(crumb.path, crumb.path);
  }
  // Keep a URL-provided folder selectable while its children are loading.
  if (value) options.set(value, value);
  for (const folder of listing?.folders ?? []) {
    options.set(folder.path, folder.path);
  }

  return (
    <FormControl size="small" error={Boolean(error)} sx={{ minWidth: 220, maxWidth: "100%" }}>
      <InputLabel id={labelId} shrink>Folder</InputLabel>
      <Select
        labelId={labelId}
        label="Folder"
        value={value ?? ""}
        displayEmpty
        onChange={(event) => onChange(event.target.value || null)}
        renderValue={(selected) => selected || "All folders"}
        sx={{ maxWidth: { xs: "100%", sm: 420 } }}
      >
        <MenuItem value="">All folders</MenuItem>
        {[...options].map(([path, label]) => (
          <MenuItem key={path} value={path} sx={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>
            {label}
          </MenuItem>
        ))}
        {loading && <MenuItem disabled>Loading folders…</MenuItem>}
      </Select>
      {error ? (
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <FormHelperText>{error}</FormHelperText>
          <Button size="small" onClick={() => setRetry((previous) => previous + 1)}>Retry</Button>
        </Box>
      ) : value ? <FormHelperText>Includes subfolders</FormHelperText> : null}
    </FormControl>
  );
}
