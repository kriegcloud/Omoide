import { useEffect, useState } from "react";
import {
  Alert,
  Avatar,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  List,
  ListItemAvatar,
  ListItemButton,
  ListItemText,
  TextField,
} from "@mui/material";
import { API } from "../config";
import { Person } from "../types";
import { encodeFilePath } from "../urlUtils";
import {
  attachMediaToPersonBulk,
  reassignMediaToPerson,
  searchPersonsByName,
} from "../services/personActions";

interface AssignMediaToPersonDialogProps {
  open: boolean;
  mediaIds: number[];
  sourcePersonId?: number;
  onClose: () => void;
  onAssigned: (person: Person, skippedCount: number) => void;
}

export default function AssignMediaToPersonDialog({
  open,
  mediaIds,
  sourcePersonId,
  onClose,
  onAssigned,
}: AssignMediaToPersonDialogProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Person[]>([]);
  const [searching, setSearching] = useState(false);
  const [assigning, setAssigning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setResults([]);
    setError(null);
  }, [open]);

  useEffect(() => {
    const name = query.trim();
    if (!open || !name) {
      setResults([]);
      return;
    }
    const controller = new AbortController();
    const timeout = window.setTimeout(() => {
      setSearching(true);
      searchPersonsByName(name, controller.signal)
        .then((people) => setResults(people.filter((person) => person.id !== sourcePersonId)))
        .catch((err) => {
          if (!(err instanceof DOMException && err.name === "AbortError")) {
            setError("Failed to search people");
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) setSearching(false);
        });
    }, 250);
    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [open, query, sourcePersonId]);

  const assign = async (person: Person) => {
    setAssigning(true);
    setError(null);
    try {
      let skippedCount = 0;
      if (sourcePersonId !== undefined && mediaIds.length === 1) {
        await reassignMediaToPerson(sourcePersonId, mediaIds[0], person.id);
      } else {
        const result = await attachMediaToPersonBulk(person.id, mediaIds);
        skippedCount = result.skipped_ids.length;
      }
      onAssigned(person, skippedCount);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to assign media");
    } finally {
      setAssigning(false);
    }
  };

  return (
    <Dialog open={open} onClose={assigning ? undefined : onClose} fullWidth maxWidth="xs">
      <DialogTitle>Assign to person</DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <TextField
          autoFocus
          fullWidth
          label="Search people"
          value={query}
          disabled={assigning}
          onChange={(event) => setQuery(event.target.value)}
          slotProps={{
            input: { endAdornment: searching ? <CircularProgress size={20} /> : null },
          }}
        />
        <List disablePadding sx={{ mt: 1 }}>
          {results.map((person) => (
            <ListItemButton key={person.id} disabled={assigning} onClick={() => void assign(person)}>
              <ListItemAvatar>
                <Avatar
                  src={person.profile_face?.thumbnail_path
                    ? `${API}/thumbnails/${encodeFilePath(person.profile_face.thumbnail_path)}`
                    : undefined}
                >
                  {person.name?.[0]?.toUpperCase() ?? "?"}
                </Avatar>
              </ListItemAvatar>
              <ListItemText primary={person.name ?? "Unknown"} secondary={`${person.appearance_count} appearances`} />
            </ListItemButton>
          ))}
        </List>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={assigning}>Cancel</Button>
      </DialogActions>
    </Dialog>
  );
}
