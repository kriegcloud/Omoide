import { useMutationRefresh } from "../context/UndoContext";
import React, { useState, useEffect, useRef } from "react";
import { TextField, Autocomplete, CircularProgress, Box, Chip } from "@mui/material";
import { Tag } from "../types";
import { getTags } from "../services/tag";
import { searchTags } from "../services/search";
import { startOperation } from "../stores/optimisticOperations";
import { createTag, assignTag, lookupMediaTagOperation } from "../services/tagging";

type OwnerType = "media" | "person";

interface TagAdderProps {
  ownerType: OwnerType;
  ownerId: number;
  existingTags: Tag[];
  onTagAdded: (newTag: Tag) => void;
}

interface TagOption extends Partial<Tag> {
  inputValue?: string;
}

const SEARCH_DEBOUNCE_MS = 300;
const SEARCH_LIMIT = 25;

export default function TagAdder({
  ownerType,
  ownerId,
  existingTags,
  onTagAdded,
}: TagAdderProps) {
  // Options come from a server-side search of the typed input so the
  // duplicate check is not limited to the first page of tags.
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const pendingRef = useRef(new Map<string, Tag>());
  const [pendingTags, setPendingTags] = useState(new Map<string, Tag>());
  const nextTemporaryId = useRef(-1);
  const latest = useRef({ ownerId, ownerType, onTagAdded });
  latest.current = { ownerId, ownerType, onTagAdded };
  const [loadError, setLoadError] = useState<string | null>(null);
  const [options, setOptions] = useState<Tag[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [loading, setLoading] = useState(false);

  const [revision, setRevision] = useState(0);
  useMutationRefresh(["tag:changed", "list:invalidate"], () => setRevision(value => value + 1));

  useEffect(() => {
    const query = inputValue.trim();
    let cancelled = false;
    setLoading(true);
    const timer = window.setTimeout(
      () => {
        const request = query
          ? searchTags(query, SEARCH_LIMIT)
          : getTags(null);
        request
          .then((page) => {
            if (!cancelled) { setOptions(page.items ?? []); setLoadError(null); }
          })
          .catch((error) => {
            if (!cancelled) setLoadError(error instanceof Error ? error.message : "Failed to load tags");
          })
          .finally(() => {
            if (!cancelled) setLoading(false);
          });
      },
      query ? SEARCH_DEBOUNCE_MS : 0
    );
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [inputValue, revision]);

  const handleSelection = async (
    event: React.SyntheticEvent,
    newValue: string | TagOption | null
  ) => {
    if (!newValue) return;

    let tagNameToProcess: string;

    // This is a "Create new tag" action
    if (typeof newValue === "object" && newValue.inputValue) {
      tagNameToProcess = newValue.inputValue;
    }
    // This is selecting an existing tag
    else if (typeof newValue === "object" && newValue.name) {
      tagNameToProcess = newValue.name;
    }
    // This is for when the user types and hits Enter without selecting
    else if (typeof newValue === "string") {
      tagNameToProcess = newValue;
    } else {
      return;
    }

    const finalTagName = tagNameToProcess.trim().toLowerCase();
    if (!finalTagName) return;

    if (existingTags.some((t) => t.name.toLowerCase() === finalTagName)) {
      console.log(`Tag "${finalTagName}" is already assigned.`);
      return;
    }

    const token = `${ownerType}:${ownerId}:${finalTagName}`;
    if (pendingRef.current.has(token)) return;
    const known = (typeof newValue === "object" && newValue.id !== undefined
      ? newValue as Tag : options.find(t => t.name.toLowerCase() === finalTagName));
    const provisional = known ?? { id: nextTemporaryId.current--, name: finalTagName } as Tag;
    pendingRef.current.set(token, provisional);
    setPendingTags(new Map(pendingRef.current));
    setInputValue("");
    const operation = startOperation({
      label: `Assign tag ${finalTagName}`,
      input: { ownerType, ownerId, name: finalTagName, known },
      resources: [`${ownerType}:${ownerId}`, `tag:${finalTagName}`], patches: [],
      ...(ownerType === "media" ? {lookup: lookupMediaTagOperation} : {}),
      request: async (input, operationId) => {
        const tag = input.known ?? await createTag(input.name);
        // Resolve the provisional identity before assignment. Failed assignment
        // never deletes the successfully created shared tag.
        pendingRef.current.set(token, tag); setPendingTags(new Map(pendingRef.current));
        const receipt = await assignTag(input.ownerType, input.ownerId, tag.id, input.ownerType === "media" ? operationId : undefined);
        const current = latest.current;
        if (mounted.current && current.ownerId === input.ownerId && current.ownerType === input.ownerType) current.onTagAdded(tag);
        return {tag, receipt};
      },
      ...(ownerType === "media" ? {receipt: (result: {receipt: unknown}) => result.receipt} : {}),
    });
    void operation.done.then(() => {
      pendingRef.current.delete(token); setPendingTags(new Map(pendingRef.current));
    });
  };

  const availableOptions = options.filter(
    (tag) => !existingTags.some((existingTag) => existingTag.id === tag.id)
  );

  return (
    <Box>
    {[...pendingTags.entries()].filter(([token]) => token.startsWith(`${ownerType}:${ownerId}:`)).map(([token, tag]) => (
      <Chip key={token} label={`${tag.name} — Saving…`} size="small" sx={{ mb: 1, mr: 1 }} />
    ))}
    <Autocomplete
      fullWidth
      freeSolo
      selectOnFocus
      clearOnBlur
      handleHomeEndKeys
      value={null}
      onChange={handleSelection}
      inputValue={inputValue}
      onInputChange={(_, newInputValue) => setInputValue(newInputValue)}
      options={availableOptions as TagOption[]}
      loading={loading}
      getOptionLabel={(option) => {
        if (typeof option === "string") return option;
        if (option.inputValue) return option.name ?? option.inputValue; // "Create ..." entry
        return option.name ?? "";
      }}
      filterOptions={(opts, params) => {
        // Options are already filtered server-side; only decide whether to
        // offer creating a new tag based on the search results.
        const filtered = [...opts] as TagOption[];
        const query = params.inputValue.trim().toLowerCase();
        const isExisting = opts.some(
          (option) =>
            typeof option !== "string" &&
            query === option.name?.toLowerCase()
        );
        if (query !== "" && !isExisting && !loading) {
          filtered.push({
            inputValue: params.inputValue,
            name: `Create "${params.inputValue}"`,
          });
        }
        return filtered;
      }}
      renderOption={(props, option) => {
        if (typeof option === "string") {
          return (
            <li {...props} key={option}>
              {option}
            </li>
          );
        }
        return (
          <li {...props} key={option.id || option.inputValue}>
            {option.name}
          </li>
        );
      }}
      renderInput={(params) => (
        <TextField
          {...params}
          variant="filled"
          size="small"
          placeholder="Add or create a tag..."
          error={!!loadError}
          helperText={loadError}
          InputProps={{
            ...params.InputProps,
            endAdornment: (
              <>
                {loading ? (
                  <CircularProgress color="inherit" size={20} />
                ) : null}
                {params.InputProps.endAdornment}
              </>
            ),
          }}
        />
      )}
    />
    </Box>
  );
}
