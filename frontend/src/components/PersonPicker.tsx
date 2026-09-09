import { useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import {
  Alert,
  Avatar,
  Box,
  CircularProgress,
  List,
  ListItemButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { API } from "../config";
import { getPeople } from "../services/person";
import { searchPersonsByName } from "../services/personActions";
import type { Person, PersonReadSimple } from "../types";
import { encodeFilePath } from "../urlUtils";

interface PersonPickerProps {
  excludeIds?: number[];
  onSelect: (person: Person) => void;
  autoFocus?: boolean;
  label?: string;
  topCount?: number;
  // Confirmation dialogs keep ownership of their selection and busy state.
  selectedId?: number;
  disabled?: boolean;
}

interface PersonPickerRowProps {
  person: Person | PersonReadSimple;
  selected?: boolean;
  highlighted?: boolean;
  disabled?: boolean;
  onSelect: () => void;
  id?: string;
  role?: "option";
  tabIndex?: number;
}

export function PersonPickerRow({
  person,
  selected = false,
  highlighted = false,
  disabled = false,
  onSelect,
  id,
  role,
  tabIndex,
}: PersonPickerRowProps) {
  const parts = person.name?.trim().split(/\s+/).filter(Boolean) ?? [];
  const initials = parts.length >= 2
    ? `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase()
    : parts[0]?.slice(0, 2).toUpperCase() || "?";

  return (
    <ListItemButton
      id={id}
      role={role ?? "button"}
      aria-selected={role === "option" ? selected : undefined}
      tabIndex={tabIndex}
      disabled={disabled}
      onClick={onSelect}
      sx={{
        p: 1,
        bgcolor: selected || highlighted ? "primary.dark" : "background.paper",
        color: selected || highlighted ? "primary.contrastText" : "text.primary",
        border: 1,
        borderColor: selected ? "primary.main" : "divider",
        borderRadius: 1,
        "&:hover, &.Mui-focusVisible": {
          bgcolor: "primary.dark",
          color: "primary.contrastText",
        },
      }}
    >
      <Stack direction="row" spacing={2} alignItems="center" sx={{ minWidth: 0 }}>
        <Avatar
          src={person.profile_face?.thumbnail_path
            ? `${API}/thumbnails/${encodeFilePath(person.profile_face.thumbnail_path)}`
            : undefined}
          alt={person.name || `Person ${person.id}`}
        >
          {initials}
        </Avatar>
        <Box sx={{ minWidth: 0 }}>
          <Typography noWrap sx={{ color: "inherit" }}>
            {person.name || `Person ${person.id}`}
          </Typography>
          <Typography variant="caption" noWrap sx={{ color: "inherit", opacity: 0.75 }}>
            {person.appearance_count ?? 0} media
          </Typography>
        </Box>
      </Stack>
    </ListItemButton>
  );
}

export default function PersonPicker({
  excludeIds = [],
  onSelect,
  autoFocus = false,
  label = "Search people",
  topCount = 5,
  selectedId,
  disabled = false,
}: PersonPickerProps) {
  const [query, setQuery] = useState("");
  const [people, setPeople] = useState<Person[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const listId = useId();
  const listRef = useRef<HTMLUListElement>(null);
  // Callers commonly pass inline arrays. Compare IDs, not array identity, so
  // parent renders do not restart a request or clear the keyboard highlight.
  const excludedKey = [...new Set(excludeIds)].sort((a, b) => a - b).join(",");
  const term = query.trim();
  const count = Math.max(0, Math.floor(topCount));

  useEffect(() => {
    const controller = new AbortController();
    const excluded = new Set(excludedKey ? excludedKey.split(",").map(Number) : []);
    setPeople([]);
    setHighlightedIndex(0);
    setLoading(true);
    setError(null);

    const loadPeople = async () => {
      if (term) {
        const results = await searchPersonsByName(term, controller.signal);
        return results.filter((person) => !excluded.has(person.id)).sort(
          (a, b) => (b.appearance_count ?? 0) - (a.appearance_count ?? 0) || b.id - a.id,
        );
      }

      const results: Person[] = [];
      let cursor: string | undefined;
      // The cursor endpoint already orders by media count, then ID, descending.
      // Continue past excluded people instead of leaving the top list short.
      while (results.length < count) {
        const page = await getPeople(cursor, false, undefined, controller.signal);
        if (controller.signal.aborted) return [];
        results.push(...(page.items as Person[]).filter((person) => !excluded.has(person.id)));
        if (!page.next_cursor || page.next_cursor === cursor) break;
        cursor = page.next_cursor;
      }
      return results.slice(0, count);
    };

    const timer = window.setTimeout(() => {
      loadPeople()
        .then((results) => {
          if (!controller.signal.aborted) setPeople(results);
        })
        .catch(() => {
          if (!controller.signal.aborted) setError("Failed to load people. Try searching again.");
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, term ? 300 : 0);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [term, excludedKey, count]);

  useEffect(() => {
    listRef.current?.children[highlightedIndex]?.scrollIntoView({ block: "nearest" });
  }, [highlightedIndex, people]);

  const handleKeyDown = (event: KeyboardEvent) => {
    // MUI activates a focused row itself. Do not select twice as Enter bubbles.
    if (event.defaultPrevented || disabled || loading || !people.length || event.nativeEvent.isComposing) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      event.stopPropagation();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      const next = (highlightedIndex + direction + people.length) % people.length;
      setHighlightedIndex(next);
      if (listRef.current?.contains(event.target as Node)) {
        (listRef.current.children[next] as HTMLElement | undefined)?.focus();
      }
    } else if (event.key === "Enter") {
      event.preventDefault();
      event.stopPropagation();
      onSelect(people[highlightedIndex]);
    }
  };

  return (
    <Box>
      <TextField
        fullWidth
        margin="dense"
        autoFocus={autoFocus}
        label={label}
        value={query}
        disabled={disabled}
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={handleKeyDown}
        slotProps={{
          input: { endAdornment: loading ? <CircularProgress size={20} /> : null },
          htmlInput: {
            role: "combobox",
            "aria-autocomplete": "list",
            "aria-expanded": people.length > 0,
            "aria-controls": listId,
            "aria-activedescendant": people[highlightedIndex]
              ? `${listId}-${people[highlightedIndex].id}` : undefined,
          },
        }}
      />
      <Typography id={`${listId}-label`} variant="subtitle2" sx={{ mt: 1, mb: 1 }}>
        {term ? "Search results" : "Most media first"}
      </Typography>
      {error && <Alert severity="error">{error}</Alert>}
      {!loading && !error && !people.length && (
        <Typography color="text.secondary" role="status">
          {term ? "No matches" : "No people available"}
        </Typography>
      )}
      <List
        ref={listRef}
        id={listId}
        role="listbox"
        aria-labelledby={`${listId}-label`}
        aria-busy={loading}
        disablePadding
        onKeyDown={handleKeyDown}
        onFocus={(event) => {
          const index = Array.from(event.currentTarget.children).findIndex(
            (row) => row.contains(event.target),
          );
          if (index >= 0) setHighlightedIndex(index);
        }}
        sx={{ display: "flex", flexDirection: "column", gap: 1, maxHeight: 360, overflowY: "auto" }}
      >
        {people.map((person, index) => (
          <PersonPickerRow
            key={person.id}
            id={`${listId}-${person.id}`}
            role="option"
            tabIndex={index === highlightedIndex ? 0 : -1}
            person={person}
            selected={selectedId === person.id}
            highlighted={highlightedIndex === index}
            disabled={disabled}
            onSelect={() => {
              setHighlightedIndex(index);
              onSelect(person);
            }}
          />
        ))}
      </List>
    </Box>
  );
}
