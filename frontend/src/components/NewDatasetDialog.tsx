import { useEffect, useState } from "react";
import {
  Autocomplete,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
} from "@mui/material";
import type { Person, TrainingDataset } from "../types";
import { createDataset } from "../services/datasets";
import { searchPersonsByName } from "../services/personActions";

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
  const [people, setPeople] = useState<Person[]>([]);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open || query.trim().length < 2) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      searchPersonsByName(query.trim(), controller.signal).then(setPeople).catch(() => undefined);
    }, 250);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [open, query]);

  const submit = async () => {
    setBusy(true);
    try {
      const dataset = await createDataset({
        name: name.trim(),
        trigger_word: trigger.trim() || undefined,
        class_token: classToken.trim() || undefined,
        person_id: person?.id,
      });
      onCreated(dataset);
      setName("");
      setTrigger("");
      setClassToken("person");
      setPerson(null);
    } finally {
      setBusy(false);
    }
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
          <Autocomplete
            options={people}
            value={person}
            onChange={(_, value) => setPerson(value)}
            onInputChange={(_, value) => setQuery(value)}
            getOptionLabel={(option) => option.name ?? `Person ${option.id}`}
            renderInput={(params) => <TextField {...params} label="Person (optional)" />}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button onClick={() => void submit()} disabled={!name.trim() || busy} variant="contained">Create dataset</Button>
      </DialogActions>
    </Dialog>
  );
}
