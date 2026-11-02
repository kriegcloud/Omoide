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
import Grid from "@mui/material/Grid";
import { DuplicateGroup as GroupType } from "../types";
import { DuplicateMediaCard } from "./DuplicateMediaCard";
import { resolveDuplicates } from "../services/duplicates";
import ConfirmDialog from "./ConfirmDialog";
import { useHotkeys } from "../hotkeys/useHotkey";
import config from "../config";
import type { SelectionClickEvent } from "../hooks/useMarqueeSelection";

interface DuplicateGroupProps {
  group: GroupType;
  focused: boolean;
  onFocusGroup: () => void;
  onGroupResolved: () => void;
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
  onGroupResolved,
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
  const [isProcessing, setIsProcessing] = useState(false);
  const [confirmAction, setConfirmAction] =
    useState<{ action: ExtendedActionType; groupId: number; masterId: number | undefined; memberIds: number[]; onResolved: () => void } | null>(null);
  const openConfirm = (action: ExtendedActionType) => {
    if (isProcessing || confirmAction || config.PRESENTATION_MODE) return;
    setConfirmAction({ action, groupId: group.group_id, masterId: validMasterId,
      memberIds: group.items.map(item => item.id), onResolved: onGroupResolved });
  };
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleResolve = async () => {
    if (!confirmAction) return;

    setIsProcessing(true);

    try {
      const requiresMaster = confirmAction.action !== "MARK_NOT_DUPLICATE";
      if (requiresMaster && confirmAction.masterId === undefined) throw new Error("This group has no remaining master. Refresh the list.");
      await resolveDuplicates(
        confirmAction.groupId,
        confirmAction.action,
        requiresMaster ? confirmAction.masterId : undefined,
        confirmAction.memberIds,
      );

      confirmAction.onResolved();
    } catch (error) {
      console.error(`Failed to resolve group ${group.group_id}:`, error);
      setErrorMessage(
        error instanceof Error ? error.message : "Failed to resolve group"
      );
    } finally {
      setIsProcessing(false);
      setConfirmAction(null);
    }
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
  React.useEffect(() => {
    if (!focused) return;
    // Run after dialog unmount/restore-focus and after react-window mounts a row.
    const frame = requestAnimationFrame(() => {
      groupRef.current?.focus({ preventScroll: true });
      groupRef.current?.scrollIntoView({ block: "nearest", behavior: "instant" });
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
  }, { scope: "page", enabled: focused && !confirmAction && !isProcessing && !config.PRESENTATION_MODE,
    when: event => event.target === groupRef.current });

  return (
    <Paper ref={groupRef} tabIndex={-1} onFocus={event => { if (event.target === event.currentTarget) onFocusGroup(); }}
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
            disabled={isProcessing}
          >
            Keep Master, Delete Rest (Files)
          </Button>
          <Button
            size="small"
            variant="outlined"
            color="warning"
            onClick={() => openConfirm("DELETE_RECORDS")}
            disabled={isProcessing}
          >
            Keep Master, Delete Rest (Records)
          </Button>
          <Button
            size="small"
            variant="outlined"
            color="secondary"
            onClick={() => openConfirm("BLACKLIST_RECORDS")}
            disabled={isProcessing}
          >
            Keep Master, Blacklist Rest
          </Button>
          <Button
            size="small"
            variant="outlined"
            color="success"
            onClick={() => openConfirm("MARK_NOT_DUPLICATE")}
            disabled={isProcessing}
          >
            Keep All, Not Duplicates
          </Button>
        </Box>
      </Box>
      <Grid container spacing={0.5} sx={{ p: 0.5 }}>
        {group.items.map((media) => (
          <Grid key={media.id} size={{ xs: 6, sm: 4, md: 3, lg: 2 }}>
            <DuplicateMediaCard
              media={media}
              groupId={group.group_id}
              isSelectedAsMaster={media.id === validMasterId}
              onSelectMaster={() => onSelectMaster(media.id)}
              selecting={selecting}
              selected={selectedIds.has(media.id)}
              onSelectionClick={onSelectionClick}
            />
          </Grid>
        ))}
      </Grid>

      {/* Confirmation Dialog */}
      <ConfirmDialog open={!!confirmAction} onClose={() => setConfirmAction(null)}
        title="Confirm Action" message={confirmAction ? `${actionText[confirmAction.action]} This action cannot be undone.` : ""}
        loading={isProcessing} onConfirm={handleResolve} />
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
