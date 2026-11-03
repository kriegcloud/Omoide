// components/DuplicateGroup.tsx

import React, { useState } from "react";
import {
  Paper,
  Typography,
  Box,
  Button,
  Snackbar,
  Alert,
  Checkbox,
  FormControlLabel,
} from "@mui/material";
import Gallery from "./Gallery";
import { DuplicateGroup as GroupType } from "../types";
import { DuplicateMediaCard } from "./DuplicateMediaCard";
import type { DuplicateResolveSnapshot } from "../services/duplicateResolver";
import ConfirmDialog from "./ConfirmDialog";
import { useHotkeys } from "../hotkeys/useHotkey";
import config from "../config";
import type { SelectionClickEvent } from "../hooks/useMarqueeSelection";

interface DuplicateGroupProps {
  group: GroupType;
  focused: boolean;
  onFocusGroup: () => void;
  /** Optimistic: the page removes the group now and resolves in the background. */
  onResolveRequested: (snapshot: DuplicateResolveSnapshot) => void;
  selecting: boolean;
  selectedIds: Set<number>;
  masterId: number;
  onSelectMaster: (mediaId: number) => void;
  onSelectionClick: (id: number, event: SelectionClickEvent) => boolean;
  onSelectGroup: (checked: boolean) => void;
}

type ActionType = "DELETE_FILES" | "DELETE_RECORDS" | "BLACKLIST_RECORDS";
type ExtendedActionType = ActionType | "MARK_NOT_DUPLICATE";

export const DuplicateGroup: React.FC<DuplicateGroupProps> = ({
  group,
  focused,
  onFocusGroup,
  onResolveRequested,
  selecting,
  selectedIds,
  masterId,
  onSelectMaster,
  onSelectionClick,
  onSelectGroup,
}) => {
  const validMasterId = group.items.some(item => item.id === masterId) ? masterId : group.items[0]?.id;
  React.useEffect(() => {
    if (validMasterId !== undefined && validMasterId !== masterId) onSelectMaster(validMasterId);
  }, [validMasterId, masterId, onSelectMaster]);
  const selectedCount = group.items.filter((media) => selectedIds.has(media.id)).length;
  const [confirmAction, setConfirmAction] =
    useState<(DuplicateResolveSnapshot & { onResolved: (snapshot: DuplicateResolveSnapshot) => void }) | null>(null);
  const openConfirm = (action: ExtendedActionType) => {
    if (confirmAction || config.PRESENTATION_MODE) return;
    setConfirmAction({ action, groupId: group.group_id, masterId: validMasterId,
      memberIds: group.items.map(item => item.id), onResolved: onResolveRequested });
  };
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Non-blocking: hand the snapshot to the page and close at once. The page
  // removes the group immediately and reports a failure if the server rejects.
  const handleResolve = () => {
    if (!confirmAction) return;
    const { onResolved, ...snapshot } = confirmAction;
    if (snapshot.action !== "MARK_NOT_DUPLICATE" && snapshot.masterId === undefined) {
      setErrorMessage("This group has no remaining master. Refresh the list.");
      setConfirmAction(null);
      return;
    }
    setConfirmAction(null);
    onResolved(snapshot);
  };

  const actionText: Record<ExtendedActionType, string> = {
    DELETE_FILES: `This will KEEP the selected master file and PERMANENTLY DELETE the other ${
      (confirmAction?.memberIds.length ?? group.items.length) - 1
    } files and their database records.`,
    DELETE_RECORDS: `This will KEEP the selected master file and only DELETE the database records for the other ${
      (confirmAction?.memberIds.length ?? group.items.length) - 1
    } files. The files will remain on disk.`,
    BLACKLIST_RECORDS: `This will KEEP the selected master file, DELETE the records for the others, and BLACKLIST their paths to prevent re-import.`,
    MARK_NOT_DUPLICATE:
      "This will KEEP every file in this group and remember they are not duplicates so future scans will skip them.",
  };

  const groupRef = React.useRef<HTMLDivElement>(null);
  const focusEffectRan = React.useRef(false);
  React.useEffect(() => {
    // false on mount: the virtual list remounts a focused row whenever it re-enters
    // the overscan (the page's scrollToIndex already placed rows reached with j/k).
    const moved = focusEffectRan.current;
    focusEffectRan.current = true;
    if (!focused) return;
    // Run after dialog unmount/restore-focus and after the virtual list mounts a row.
    const frame = requestAnimationFrame(() => {
      const node = groupRef.current;
      if (!node) return;
      const active = document.activeElement;
      // A remount must not pull the window to the row or take focus from another control.
      if (!moved && active instanceof HTMLElement && active !== document.body && active.dataset.duplicateGroup === undefined) return;
      node.focus({ preventScroll: true });
      if (moved) node.scrollIntoView({ block: "nearest", behavior: "instant" });
    });
    return () => cancelAnimationFrame(frame);
  }, [focused]);
  useHotkeys([
    { key: "Enter", description: "Keep master, delete the other files…", destructive: true },
    { key: "n", description: "Keep all, mark as not duplicates…", destructive: true },
    ...Array.from({ length: 9 }, (_, index) => ({ key: String(index + 1), description: `Choose master at position ${index + 1}` })),
  ], event => {
    if (event.key === "Enter") openConfirm("DELETE_FILES");
    else if (event.key.toLowerCase() === "n") openConfirm("MARK_NOT_DUPLICATE");
    else {
      const media = group.items[Number(event.key) - 1];
      if (media) onSelectMaster(media.id);
    }
  }, { scope: "page", enabled: focused && !confirmAction && !config.PRESENTATION_MODE,
    when: event => event.target === groupRef.current });

  return (
    <Paper ref={groupRef} tabIndex={-1} data-duplicate-group="" onFocus={event => { if (event.target === event.currentTarget) onFocusGroup(); }}
      aria-label={`Group ${group.group_id}, ${group.items.length} items`} variant="outlined"
      sx={{ outline: focused ? "2px solid" : undefined, outlineColor: "primary.main", outlineOffset: 0 }}>
      <Box
        sx={{
          p: 1,
          // An opaque sticky header must follow the card's inner corner;
          // square paint here used to cover the rounded border/focus ring.
          borderRadius: "3px 3px 0 0",
          borderBottom: "1px solid",
          borderColor: "divider",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 2,
        }}
      >
        <Box>
          <Typography variant="h6">
            Group {group.group_id} ({group.items.length} items)
          </Typography>
          <FormControlLabel
            data-no-marquee
            control={
              <Checkbox
                size="small"
                checked={selectedCount === group.items.length}
                indeterminate={selectedCount > 0 && selectedCount < group.items.length}
                onChange={(_, checked) => onSelectGroup(checked)}
                inputProps={{ "aria-label": `Select all in group ${group.group_id}` }}
              />
            }
            label={<Typography variant="body2">Select all in group</Typography>}
          />
        </Box>
        <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
          <Button
            size="small"
            variant="contained"
            onClick={() => openConfirm("DELETE_FILES")}
            color="error"
          >
            Keep Master, Delete Rest (Files)
          </Button>
          <Button
            size="small"
            variant="outlined"
            color="warning"
            onClick={() => openConfirm("DELETE_RECORDS")}
          >
            Keep Master, Delete Rest (Records)
          </Button>
          <Button
            size="small"
            variant="outlined"
            color="secondary"
            onClick={() => openConfirm("BLACKLIST_RECORDS")}
          >
            Keep Master, Blacklist Rest
          </Button>
          <Button
            size="small"
            variant="outlined"
            color="success"
            onClick={() => openConfirm("MARK_NOT_DUPLICATE")}
          >
            Keep All, Not Duplicates
          </Button>
        </Box>
      </Box>
      <Box sx={{ p: 0.5 }}><Gallery>
        {group.items.map((media) => (
          <Box key={media.id}>
            <DuplicateMediaCard
              media={media}
              groupId={group.group_id}
              isSelectedAsMaster={media.id === validMasterId}
              onSelectMaster={() => onSelectMaster(media.id)}
              selecting={selecting}
              selected={selectedIds.has(media.id)}
              onSelectionClick={onSelectionClick}
            />
          </Box>
        ))}
      </Gallery></Box>

      {/* Confirmation Dialog */}
      <ConfirmDialog open={!!confirmAction} onClose={() => setConfirmAction(null)}
        title="Confirm Action" message={confirmAction ? `${actionText[confirmAction.action]} This action cannot be undone.` : ""}
        onConfirm={handleResolve} />
      <Snackbar
        open={!!errorMessage}
        autoHideDuration={6000}
        onClose={() => setErrorMessage(null)}
      >
        <Alert severity="error" onClose={() => setErrorMessage(null)}>
          {errorMessage}
        </Alert>
      </Snackbar>
    </Paper>
  );
};
