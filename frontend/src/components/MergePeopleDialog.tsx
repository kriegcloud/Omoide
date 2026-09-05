import { useEffect, useMemo, useState } from "react";
import {
  Avatar,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { API } from "../config";
import { searchPersonsByName } from "../services/personActions";
import { getPeople } from "../services/person";
import { Person, PersonReadSimple } from "../types";
import { encodeFilePath } from "../urlUtils";

interface MergePeopleDialogProps {
  open: boolean;
  selectedPeople: PersonReadSimple[];
  merging: boolean;
  onClose: () => void;
  onConfirm: (target: PersonReadSimple | Person) => void;
}

const getInitials = (name?: string) => {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase();
  }
  return parts[0]?.slice(0, 2).toUpperCase() || "?";
};

interface CandidateRowProps {
  person: PersonReadSimple | Person;
  selected: boolean;
  onSelect: () => void;
}

function CandidateRow({ person, selected, onSelect }: CandidateRowProps) {
  return (
    <Box
      onClick={onSelect}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect();
        }
      }}
      sx={{
        p: 1,
        bgcolor: selected ? "primary.dark" : "background.paper",
        color: selected ? "primary.contrastText" : "text.primary",
        border: 1,
        borderColor: selected ? "primary.main" : "divider",
        borderRadius: 1,
        cursor: "pointer",
        "&:hover": {
          bgcolor: "primary.dark",
          color: "primary.contrastText",
        },
      }}
    >
      <Stack direction="row" spacing={2} alignItems="center">
        <Avatar
          src={
            person.profile_face?.thumbnail_path
              ? `${API}/thumbnails/${encodeFilePath(
                  person.profile_face.thumbnail_path,
                )}`
              : undefined
          }
          alt={person.name ?? `Person ${person.id}`}
        >
          {getInitials(person.name)}
        </Avatar>
        <Box sx={{ minWidth: 0 }}>
          <Typography noWrap sx={{ color: "inherit" }}>
            {person.name ?? "Unknown"}
          </Typography>
          <Typography
            variant="caption"
            noWrap
            sx={{ color: "inherit", opacity: 0.75 }}
          >
            {person.appearance_count ?? 0} appearance
            {(person.appearance_count ?? 0) === 1 ? "" : "s"}
          </Typography>
        </Box>
      </Stack>
    </Box>
  );
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
  const [searchTerm, setSearchTerm] = useState("");
  const [searchResults, setSearchResults] = useState<Person[]>([]);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    if (!open) return;
    setTarget(defaultTarget);
    setSearchTerm("");
    setSearchResults([]);
  }, [open, defaultTarget]);

  useEffect(() => {
    const trimmed = searchTerm.trim();
    if (!open) {
      setSearchResults([]);
      setSearching(false);
      return;
    }

    const controller = new AbortController();
    // With no search term, suggest the most-seen people (the list endpoint is
    // ordered by appearance count) so "assign these Unknowns to X" is one
    // click; a typed term searches by name instead.
    const timeout = window.setTimeout(() => {
      setSearching(true);
      const request = trimmed
        ? searchPersonsByName(trimmed, controller.signal)
        : getPeople().then((page) => page.items as unknown as Person[]);
      request
        .then((people) => {
          if (controller.signal.aborted) return;
          const selected = new Set(selectedPeople.map((person) => person.id));
          const ordered = people
            .filter((person) => !selected.has(person.id))
            .sort(
              (a, b) =>
                (b.appearance_count ?? 0) - (a.appearance_count ?? 0) || b.id - a.id,
            );
          setSearchResults(trimmed ? ordered : ordered.slice(0, 5));
        })
        .catch((error) => {
          if (error instanceof DOMException && error.name === "AbortError") {
            return;
          }
          console.error("Failed to search people", error);
          setSearchResults([]);
        })
        .finally(() => {
          if (!controller.signal.aborted) setSearching(false);
        });
    }, 300);

    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [open, searchTerm, selectedPeople]);

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
            <CandidateRow
              key={person.id}
              person={person}
              selected={target?.id === person.id}
              onSelect={() => setTarget(person)}
            />
          ))}
        </Stack>
        <TextField
          label="Search for another person..."
          fullWidth
          value={searchTerm}
          onChange={(event) => setSearchTerm(event.target.value)}
          disabled={merging}
          slotProps={{
            input: {
              endAdornment: searching ? <CircularProgress size={20} /> : null,
            },
          }}
          sx={{ mb: searchResults.length > 0 ? 2 : 0 }}
        />
        {searchResults.length > 0 && (
          <Stack spacing={1}>
            <Typography variant="subtitle2">
              {searchTerm.trim() ? "Search results" : "Most media first"}
            </Typography>
            {searchResults.map((person) => (
              <CandidateRow
                key={person.id}
                person={person}
                selected={target?.id === person.id}
                onSelect={() => setTarget(person)}
              />
            ))}
          </Stack>
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
