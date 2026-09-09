import { useEffect, useMemo, useState } from "react";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  Typography,
} from "@mui/material";
import { Person, PersonReadSimple } from "../types";
import PersonPicker, { PersonPickerRow } from "./PersonPicker";

interface MergePeopleDialogProps {
  open: boolean;
  selectedPeople: PersonReadSimple[];
  merging: boolean;
  onClose: () => void;
  onConfirm: (target: PersonReadSimple | Person) => void;
}

export default function MergePeopleDialog({
  open,
  selectedPeople,
  merging,
  onClose,
  onConfirm,
}: MergePeopleDialogProps) {
  const defaultTarget = useMemo(
    () =>
      selectedPeople.reduce<PersonReadSimple | null>((best, person) => {
        if (!best) return person;
        return (person.appearance_count ?? 0) >
          (best.appearance_count ?? 0)
          ? person
          : best;
      }, null),
    [selectedPeople],
  );
  const [target, setTarget] = useState<PersonReadSimple | Person | null>(null);

  useEffect(() => {
    if (!open) return;
    setTarget(defaultTarget);
  }, [open, defaultTarget]);

  return (
    <Dialog open={open} onClose={merging ? undefined : onClose} fullWidth>
      <DialogTitle>Merge selected people into...</DialogTitle>
      <DialogContent>
        <Typography color="text.secondary" sx={{ mb: 2 }}>
          Choose the person who should remain. This action cannot be undone.
        </Typography>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>
          Selected people
        </Typography>
        <Stack spacing={1} sx={{ mb: 2 }}>
          {selectedPeople.map((person) => (
            <PersonPickerRow
              key={person.id}
              person={person}
              selected={target?.id === person.id}
              disabled={merging}
              onSelect={() => setTarget(person)}
            />
          ))}
        </Stack>
        {open && (
          <PersonPicker
            excludeIds={selectedPeople.map((person) => person.id)}
            label="Search for another person..."
            selectedId={target?.id}
            disabled={merging}
            onSelect={setTarget}
          />
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={merging}>
          Cancel
        </Button>
        <Button
          onClick={() => target && onConfirm(target)}
          color="primary"
          variant="contained"
          disabled={
            !target ||
            merging ||
            // Nothing to merge when the only selected person is the target.
            selectedPeople.every((person) => person.id === target.id)
          }
        >
          {merging ? "Merging..." : "Confirm Merge"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
