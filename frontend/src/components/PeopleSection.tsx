import React, { Suspense, useState } from "react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Snackbar,
  Typography,
} from "@mui/material";
import { Person, Face } from "../types";
import PersonCard from "./PersonCard";
import config from "../config";
import PersonPicker from "./PersonPicker";
import { AddMediaAppearanceResult } from "../services/personActions";

const DetectedFaces = React.lazy(() => import("./DetectedFaces"));

interface PeopleSectionProps {
  persons: Person[];
  orphans: Face[];
  onAssign: (faceIds: number[], personId: number) => Promise<void>;
  onCreateFace: (faceIds: number[], name?: string) => Promise<Person>;
  onDeleteFace: (faceIds: number[]) => Promise<void>;
  onDetachFace: (faceIds: number[]) => Promise<void>;
  onAttachMediaToPerson?: (
    personId: number
  ) => Promise<AddMediaAppearanceResult | void>;
}

const SectionLoader = () => (
  <Box
    sx={{
      display: "flex",
      justifyContent: "center",
      alignItems: "center",
      height: "200px",
    }}
  >
    <CircularProgress />
  </Box>
);

export function PeopleSection({
  persons,
  orphans,
  onAssign,
  onCreateFace,
  onDeleteFace,
  onDetachFace,
  onAttachMediaToPerson,
}: PeopleSectionProps) {
  const [attachDialogOpen, setAttachDialogOpen] = useState(false);
  const [selectedPerson, setSelectedPerson] = useState<Person | null>(null);
  const [isAttaching, setIsAttaching] = useState(false);
  const [snackbar, setSnackbar] = useState<{
    open: boolean;
    message: string;
    severity: "success" | "info" | "error";
  }>({ open: false, message: "", severity: "success" });

  const resetAttachDialog = () => {
    setAttachDialogOpen(false);
    setSelectedPerson(null);
  };

  const handleAttachConfirm = async () => {
    if (!onAttachMediaToPerson || !selectedPerson) {
      return;
    }
    setIsAttaching(true);
    try {
      const result = await onAttachMediaToPerson(selectedPerson.id);
      if (result && result.added === false) {
        setSnackbar({
          open: true,
          message:
            "This media is already linked to that person or has a detected face match.",
          severity: "info",
        });
      } else {
        setSnackbar({
          open: true,
          message: "Media attached to person.",
          severity: "success",
        });
      }
      resetAttachDialog();
    } catch (err) {
      console.error("Failed to attach media to person:", err);
      setSnackbar({
        open: true,
        message: "Failed to attach media to person.",
        severity: "error",
      });
    } finally {
      setIsAttaching(false);
    }
  };

  return (
    <>
      {!config.PRESENTATION_MODE && onAttachMediaToPerson && (
        <Box mb={2} sx={{ display: "flex", justifyContent: "flex-end" }}>
          <Button variant="outlined" onClick={() => setAttachDialogOpen(true)}>
            Attach Media to Person
          </Button>
        </Box>
      )}

      {persons && persons.length > 0 && (
        <Box mb={4}>
          <Typography variant="h6" gutterBottom>
            Detected Persons
          </Typography>
          <Box sx={{ display: "flex", overflowX: "auto", gap: 2, py: 1 }}>
            {persons.map((p) => (
              <Box
                key={p.id}
                sx={{
                  width: "140px",
                  flexShrink: 0,
                }}
              >
                <PersonCard person={p} />
              </Box>
            ))}
          </Box>
        </Box>
      )}

      {/* Unassigned Faces Section */}
      {orphans.length > 0 && !config.PRESENTATION_MODE && (
        <Box id="unassigned-faces" mb={4}>
          <Suspense fallback={<SectionLoader />}>
            <DetectedFaces
              isProcessing={false}
              title="Unassigned Faces"
              faces={orphans}
              onAssign={onAssign}
              onDelete={onDeleteFace}
              onDetach={onDetachFace}
              onCreateMultiple={onCreateFace}
            />
          </Suspense>
        </Box>
      )}

      <Dialog
        open={attachDialogOpen}
        onClose={isAttaching ? undefined : resetAttachDialog}
        fullWidth
        maxWidth="sm"
      >
        <DialogTitle>Attach Media to Person</DialogTitle>
        <DialogContent>
          {attachDialogOpen && (
            <PersonPicker
              autoFocus
              label="Search for person"
              excludeIds={persons.map((person) => person.id)}
              selectedId={selectedPerson?.id}
              disabled={isAttaching}
              onSelect={setSelectedPerson}
            />
          )}
          {selectedPerson && (
            <Box sx={{ mt: 2, display: "flex", alignItems: "center", gap: 1 }}>
              <Typography>
                Selected: {selectedPerson.name || `Person ${selectedPerson.id}`}
              </Typography>
              <Button disabled={isAttaching} onClick={() => setSelectedPerson(null)}>Clear person</Button>
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={resetAttachDialog} disabled={isAttaching}>
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={handleAttachConfirm}
            disabled={!selectedPerson || isAttaching}
          >
            {isAttaching ? "Attaching..." : "Attach"}
          </Button>
        </DialogActions>
      </Dialog>

      <Snackbar
        open={snackbar.open}
        autoHideDuration={3500}
        onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
      >
        <Alert
          severity={snackbar.severity}
          onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
          sx={{ width: "100%" }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </>
  );
}
