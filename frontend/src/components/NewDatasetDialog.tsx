import { useState } from "react";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import type { Person, TrainingDataset } from "../types";
import { createDataset } from "../services/datasets";
import PersonPicker from "./PersonPicker";

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (dataset: TrainingDataset) => void;
}

export default function NewDatasetDialog({ open, onClose, onCreated }: Props) {
  const [name, setName] = useState("");
  const [trigger, setTrigger] = useState("");
  const [classToken, setClassToken] = useState("person");
  const [person, setPerson] = useState<Person | null>(null);
  const busy = false;

  const submit = async () => {
    const input = {
      name: name.trim(), trigger_word: trigger.trim() || undefined,
      class_token: classToken.trim() || undefined, person_id: person?.id,
    };
    // Reset only the captured draft, before a later opening can own new input.
    setName(""); setTrigger(""); setClassToken("person"); setPerson(null);
    onClose();
    try { onCreated(await createDataset(input)); }
    catch { /* Shared mutation feedback owns failure after this dialog closes. */ }
  };

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} fullWidth maxWidth="sm">
      <DialogTitle>New training dataset</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <TextField label="Name" value={name} onChange={(event) => setName(event.target.value)} autoFocus required />
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
            <TextField fullWidth label="Trigger word" value={trigger} onChange={(event) => setTrigger(event.target.value)} helperText="Leave blank to derive from the name" />
            <TextField fullWidth label="Class" value={classToken} onChange={(event) => setClassToken(event.target.value)} />
          </Stack>
          {open && (
            <PersonPicker
              label="Person (optional)"
              selectedId={person?.id}
              disabled={busy}
              onSelect={setPerson}
            />
          )}
          {person && (
            <Stack direction="row" spacing={1} alignItems="center">
              <Typography>Selected: {person.name || `Person ${person.id}`}</Typography>
              <Button disabled={busy} onClick={() => setPerson(null)}>Clear person</Button>
            </Stack>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button onClick={() => void submit()} disabled={!name.trim() || busy} variant="contained">Create dataset</Button>
      </DialogActions>
    </Dialog>
  );
}
