import React, { useEffect, useRef, useState } from "react";
import { startOperation } from "../stores/optimisticOperations";
import { Link } from "react-router-dom";
import { Media, Tag } from "../types";
import { Person } from "../types";
import { Chip, Box, Typography } from "@mui/material";
import {
  removeTagFromMedia,
  removeTagFromPerson,
} from "../services/tagActions";
import config from "../config";

export interface TagsProps {
  media?: Media;
  person?: Person;
  onUpdate: (updated: Media | Person) => void;
}

export function Tags({ media, person, onUpdate }: Readonly<TagsProps>) {
  const owner = media || person;
  const ownerType = media ? "media" : "person";
  const latest = useRef({ owner, ownerType, onUpdate });
  latest.current = { owner, ownerType, onUpdate };
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const pendingRef = useRef(new Set<string>());
  const [pending, setPending] = useState(new Set<string>());
  if (!owner) {
    return null;
  }

  const handleRemove = (tagToRemove: Tag) => {
    const ownerType = media ? "media" : "person";
    const target = owner.id;
    const token = `${ownerType}:${target}:${tagToRemove.id}`;
    if (pendingRef.current.has(token)) return;
    pendingRef.current.add(token); setPending(new Set(pendingRef.current));
    const operation = startOperation({
      label: `Remove tag ${tagToRemove.name}`,
      input: { target, tagId: tagToRemove.id },
      resources: [`${ownerType}:${target}`, `tag:${tagToRemove.id}`], patches: [],
      request: async ({ target, tagId }) => {
        if (ownerType === "media") await removeTagFromMedia(target, tagId);
        else await removeTagFromPerson(target, tagId);
        const current = latest.current;
        if (mounted.current && current.owner?.id === target && current.ownerType === ownerType) {
          current.onUpdate({ ...current.owner, tags: (current.owner.tags ?? []).filter(t => t.id !== tagId) });
        }
      },
    });
    void operation.done.then(() => {
      pendingRef.current.delete(token); setPending(new Set(pendingRef.current));
    });
  };
  return (
    <Box component="section" sx={{ mt: 2 }}>
      <Typography variant="h6" component="h2" sx={{ mb: 1, fontWeight: "600" }}>
        Tags
      </Typography>
      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1 }}>
        {(owner.tags ?? []).filter(tag => !pending.has(`${media ? "media" : "person"}:${owner.id}:${tag.id}`)).map((tag) => (
          <Chip
            key={tag.id}
            label={tag.name}
            // This makes the whole chip a clickable link
            component={Link}
            to={`/tag/${tag.id}`}
            clickable
            // The onDelete prop adds a delete icon and handles the click
            onDelete={
              config.PRESENTATION_MODE
                ? undefined
                : (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    handleRemove(tag);
                  }
            }
            // Use the theme's accent color for a consistent look
            sx={{
              color: "accent.dark",
              fontWeight: 500,
              borderColor: "accent.dark",
              "& .MuiChip-deleteIcon": {
                color: "accent.dark",
                "&:hover": {
                  color: "accent.dark", // A darker shade on hover
                },
              },
            }}
            variant="outlined"
          />
        ))}
      </Box>
    </Box>
  );
}
