import Gallery from "../components/Gallery";
import GalleryPage from "../components/GalleryPage";
import { useMutationRefresh } from "../context/UndoContext";
import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardActionArea,
  CardContent,
  Chip,
  CircularProgress,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import DatasetIcon from "@mui/icons-material/Dataset";
import { Link } from "react-router-dom";
import { API } from "../config";
import { EmptyState } from "../components/EmptyState";
import NewDatasetDialog from "../components/NewDatasetDialog";
import CurationEntryLink from "../components/CurationEntryLink";
import { getDatasets } from "../services/datasets";
import type { TrainingDataset } from "../types";
import { encodeFilePath } from "../urlUtils";

export default function DatasetsPage() {
  const [datasets, setDatasets] = useState<TrainingDataset[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newOpen, setNewOpen] = useState(false);

  const [revision, setRevision] = useState(0);
  const requestVersion = useRef(0);
  useMutationRefresh(["dataset:changed", "media:deleted", "media:updated", "media:moved", "person:changed", "tag:changed", "list:invalidate"], () => {
    requestVersion.current += 1;
    setRevision(value => value + 1);
  });
  useEffect(() => {
    const version = ++requestVersion.current;
    setError(null);
    getDatasets().then(next => { if (version === requestVersion.current) setDatasets(next); })
      .catch(reason => { if (version === requestVersion.current) setError(reason instanceof Error ? reason.message : "Failed to load datasets"); })
      .finally(() => { if (version === requestVersion.current) setLoading(false); });
    return () => { requestVersion.current += 1; };
  }, [revision]);

  return (
    <GalleryPage title={"Training datasets"} count={datasets.length}>
      <Box display="flex" justifyContent="space-between" alignItems="center" mb={3}>
        <Box display="flex" alignItems="center" gap={1}>
          <DatasetIcon color="primary" />

        </Box>
        <Box display="flex" gap={1} flexWrap="wrap"><CurationEntryLink /><Button variant="contained" startIcon={<AddIcon />} onClick={() => setNewOpen(true)}>New dataset</Button></Box>
      </Box>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {loading ? (
        <Box display="grid" minHeight="40vh" sx={{ placeItems: "center" }}><CircularProgress /></Box>
      ) : datasets.length === 0 ? (
        <EmptyState icon={<DatasetIcon />} title="No training datasets" description="Create a dataset from a person or start one here, then add images from any media grid." />
      ) : (
        <Box><Gallery>
          {datasets.map((dataset) => (
            <Card key={dataset.id} elevation={0} sx={{ borderRadius: 1, border: 1, borderColor: "divider", overflow: "hidden" }}>
              <CardActionArea component={Link} to={`/dataset/${dataset.id}`}>
                <Box sx={{ aspectRatio: "16 / 10", bgcolor: "action.hover", display: "grid", placeItems: "center", overflow: "hidden" }}>
                  {dataset.cover?.thumbnail_path ? (
                    <Box component="img" loading="lazy" decoding="async" src={`${API}/thumbnails/${encodeFilePath(dataset.cover.thumbnail_path)}`} alt="" sx={{ width: "100%", height: "100%", objectFit: "cover" }} />
                  ) : <DatasetIcon color="disabled" sx={{ fontSize: 56 }} />}
                </Box>
                <CardContent>
                  <Typography variant="h6" fontWeight={700} noWrap>{dataset.name}</Typography>
                  <Typography variant="body2" color="text.secondary" noWrap>{dataset.trigger_word} · {dataset.class_token}</Typography>
                  <Box display="flex" gap={0.75} mt={1.5} flexWrap="wrap">
                    <Chip size="small" label={`${dataset.included_count}/${dataset.item_count} items`} />
                    <Chip size="small" label={dataset.last_export ? `Last export: ${dataset.last_export.status}` : "Not exported"} color={dataset.last_export?.status === "completed" ? "success" : "default"} />
                  </Box>
                </CardContent>
              </CardActionArea>
            </Card>
          ))}
        </Gallery></Box>
      )}
      <NewDatasetDialog open={newOpen} onClose={() => setNewOpen(false)} onCreated={(dataset) => { setDatasets((current) => [dataset, ...current]); setNewOpen(false); }} />
    </GalleryPage>
  );
}
