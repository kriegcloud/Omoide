import { startReviewResolve } from "../services/review";
import { SelectionHotkeyDialogs } from "../hotkeys/SelectionHotkeyDialogs";
import { useHotkeys, useHotkey } from "../hotkeys/useHotkey";
import { selectionBindings } from "../hotkeys/keymap";
import config from "../config";
import React, { useLayoutEffect, useState } from "react";
import {
  Box,
  Button,
  Divider,
  Menu,
  MenuItem,
  Paper,
  type PaperProps,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import DeleteIcon from "@mui/icons-material/Delete";
import DeleteForeverIcon from "@mui/icons-material/DeleteForever";
import BlockIcon from "@mui/icons-material/Block";
import SelectAllIcon from "@mui/icons-material/SelectAll";
import ClearAllIcon from "@mui/icons-material/ClearAll";
import ArrowDropDownIcon from "@mui/icons-material/ArrowDropDown";
import PersonAddIcon from "@mui/icons-material/PersonAdd";

import AssignMediaToPersonDialog from "./AssignMediaToPersonDialog";
import ConfirmDialog from "./ConfirmDialog";
import { formatBytes } from "../formatUtils";

/** Pins page actions below the actual header, including responsive/search layouts. */
interface StickySelectionToolbarProps extends PaperProps {
  /** With onClearSelection: shows "Clear selection (n)" and binds Escape while n > 0. */
  selectedCount?: number;
  onClearSelection?: () => void;
}

export function StickySelectionToolbar({ sx = [], selectedCount = 0, onClearSelection, children, ...props }: StickySelectionToolbarProps) {
  const [headerHeight, setHeaderHeight] = useState(0);
  useLayoutEffect(() => {
    const header = document.querySelector<HTMLElement>(".MuiAppBar-root");
    if (!header) return;
    const measure = () => setHeaderHeight(header.getBoundingClientRect().height);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(header);
    return () => observer.disconnect();
  }, []);
  const canClear = Boolean(onClearSelection) && selectedCount > 0;
  useHotkey({ key: "Escape" }, () => onClearSelection?.(), {
    scope: "page", enabled: canClear, description: "Clear the selection",
  });

  return <Paper {...props} sx={[
    {
      position: "sticky",
      top: headerHeight,
      zIndex: (theme) => theme.zIndex.appBar - 1,
      bgcolor: "background.paper",
      backdropFilter: "none",
      boxShadow: 1,
      display: canClear ? "flex" : undefined,
      alignItems: "center",
      gap: 1,
    },
    ...(Array.isArray(sx) ? sx : [sx]),
  ]}>
    {canClear && (
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        startIcon={<ClearAllIcon />}
        onClick={onClearSelection}
        aria-label={`Clear selection (${selectedCount} selected)`}
        sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
      >
        Clear ({selectedCount})
      </Button>
    )}
    {canClear ? <Box sx={{ flex: 1, minWidth: 0 }}>{children}</Box> : children}
  </Paper>;
}

export type BulkResolveAction = "DELETE_FILES" | "DELETE_RECORDS" | "BLACKLIST_RECORDS";

export type FeedbackSeverity = "success" | "error" | "warning";

export const resolveConfirmCopy = (action: BulkResolveAction, count: number) => {
  switch (action) {
    case "DELETE_FILES":
      return {
        title: "Delete files from disk",
        message: `Permanently delete ${count} file(s) from disk and remove them from the library. This cannot be undone.`,
        confirmLabel: "Delete files",
      };
    case "DELETE_RECORDS":
      return {
        title: "Remove records",
        message: `Remove ${count} record(s) from the library. The files stay on disk and may be re-imported by a future scan.`,
        confirmLabel: "Remove records",
      };
    case "BLACKLIST_RECORDS":
      return {
        title: "Blacklist items",
        message: `Blacklist ${count} item(s) so they are never re-imported and remove their records from the library. The files stay on disk.`,
        confirmLabel: "Blacklist",
      };
  }
};

interface BulkResolveToolbarProps {
  statsText: string;
  shownCount: number;
  shownSize: number;
  total: number;
  selectedIds: Set<number>;
  onSelectVisible: () => void;
  onClearSelection: () => void;
  resolve: (params: {
    action: BulkResolveAction;
    mediaIds?: number[];
    selectAll?: boolean;
  }) => Promise<{ removed: number; processed_ids?: number[] }>;
  onResolved: (removedIds: number[], removed: number, selectAll: boolean) => void;
  onAssigned?: (mediaIds: number[], skippedCount: number) => void;
  onFeedback: (message: string, severity: FeedbackSeverity) => void;
  extraActions?: React.ReactNode;
  /** The shared operation feedback owns settlement for non-blocking mutations. */
  deferredFeedback?: boolean;
  refresh?: () => Promise<void>;
}

const BulkResolveToolbar: React.FC<BulkResolveToolbarProps> = ({
  statsText,
  shownCount,
  shownSize,
  total,
  selectedIds,
  onSelectVisible,
  onClearSelection,
  resolve,
  onResolved,
  onAssigned,
  onFeedback,
  extraActions,
  deferredFeedback = false,
  refresh,
}) => {
  const [pending, setPending] = useState<{ action: BulkResolveAction; selectAll: boolean; ids: number[]; count: number; resolve: BulkResolveToolbarProps["resolve"]; refresh?: () => Promise<void>; onResolved: BulkResolveToolbarProps["onResolved"] } | null>(null);
  const [allMenuAnchor, setAllMenuAnchor] = useState<null | HTMLElement>(null);
  const [assignIds, setAssignIds] = useState<number[] | null>(null);

  const selectedCount = selectedIds.size;

  const requestAction = (action: BulkResolveAction, selectAll = false) => {
    setAllMenuAnchor(null);
    if (!selectAll && selectedCount === 0) return;
    if (config.PRESENTATION_MODE) return;
    setPending({ action, selectAll, ids: [...selectedIds], count: selectAll ? total : selectedCount, resolve, refresh, onResolved });
  };

  useHotkeys(selectionBindings.filter(binding => ["Delete", "x", "a"].includes(binding.key)), (event) => {
    if (event.key === "Delete") requestAction(event.shiftKey ? "DELETE_RECORDS" : "DELETE_FILES");
    else if (event.key.toLowerCase() === "x") requestAction("BLACKLIST_RECORDS");
    else setAssignIds(Array.from(selectedIds));
  }, { scope: "page", enabled: selectedCount > 0 && !config.PRESENTATION_MODE });
  useHotkey({ key: "Escape" }, onClearSelection, { scope: "page", enabled: selectedCount > 0, description: "Clear selection" });

  const handleConfirm = () => {
    if (!pending || config.PRESENTATION_MODE) return;
    const snapshot = pending;
    setPending(null);
    const params = snapshot.selectAll
      ? { action: snapshot.action, selectAll: true }
      : { action: snapshot.action, mediaIds: snapshot.ids };
    onClearSelection?.();
    if (deferredFeedback) {
      // Existing consumers already submit to the shared controller.
      void snapshot.resolve(params).catch(error => onFeedback(error instanceof Error ? error.message : "Action failed", "error"));
    } else {
      let result: Awaited<ReturnType<typeof resolve>> | undefined;
      return startReviewResolve({
        label: snapshot.selectAll ? "Review all matching items" : `Review ${snapshot.ids.length} selected item(s)`,
        ids: snapshot.selectAll ? [] : snapshot.ids,
        request: async () => {
          result = await snapshot.resolve(params);
          return result;
        },
        refresh: snapshot.refresh ?? (async () => {
          if (result) snapshot.onResolved(result.processed_ids ?? [], result.removed, snapshot.selectAll || !Array.isArray(result.processed_ids));
        }),
      }).done;
    }
  };

  const copy = pending
    ? resolveConfirmCopy(pending.action, pending.count)
    : null;

  return (
    <>
      <SelectionHotkeyDialogs mediaIds={[...selectedIds]} enabled />
      <StickySelectionToolbar variant="outlined" sx={{ mb: 3 }}>
        <Stack
          direction={{ xs: "column", md: "row" }}
          spacing={2}
          alignItems={{ md: "center" }}
          justifyContent="space-between"
          sx={{ p: 2 }}
        >
          <Stack direction="row" spacing={2} alignItems="center" flexWrap="wrap">
            <Typography variant="subtitle1">{statsText}</Typography>
            <Typography variant="body2" color="text.secondary">
              shown: {shownCount} · {formatBytes(shownSize)}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              selected: {selectedCount}
            </Typography>
          </Stack>
          <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
            <Button
              size="small"
              startIcon={<SelectAllIcon />}
              onClick={onSelectVisible}
              disabled={shownCount === 0}
            >
              Select visible
            </Button>
            <Button
              size="small"
              startIcon={<ClearAllIcon />}
              onClick={onClearSelection}
              disabled={selectedCount === 0}
            >
              Clear selection
            </Button>
            <Button
              size="small"
              variant="outlined"
              startIcon={<PersonAddIcon />}
              onClick={() => setAssignIds(Array.from(selectedIds))}
              disabled={selectedCount === 0}
            >
              Assign to person…
            </Button>
            {extraActions}
            <Divider flexItem orientation="vertical" sx={{ display: { xs: "none", sm: "block" } }} />
            <Tooltip title="Delete files from disk">
              <span>
                <Button
                  size="small"
                  variant="contained"
                  color="error"
                  startIcon={<DeleteForeverIcon />}
                  onClick={() => requestAction("DELETE_FILES")}
                  disabled={selectedCount === 0}
                >
                  Delete files
                </Button>
              </span>
            </Tooltip>
            <Tooltip title="Remove records from library (keep files)">
              <span>
                <Button
                  size="small"
                  variant="outlined"
                  color="error"
                  startIcon={<DeleteIcon />}
                  onClick={() => requestAction("DELETE_RECORDS")}
                  disabled={selectedCount === 0}
                >
                  Remove records
                </Button>
              </span>
            </Tooltip>
            <Tooltip title="Blacklist and remove from library">
              <span>
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<BlockIcon />}
                  onClick={() => requestAction("BLACKLIST_RECORDS")}
                  disabled={selectedCount === 0}
                >
                  Blacklist
                </Button>
              </span>
            </Tooltip>
            <Tooltip title="Apply an action to every item matching the current filters, not just the loaded ones">
              <span>
                <Button
                  size="small"
                  color="error"
                  endIcon={<ArrowDropDownIcon />}
                  onClick={(e) => setAllMenuAnchor(e.currentTarget)}
                  disabled={total === 0}
                >
                  All {total.toLocaleString()} matching
                </Button>
              </span>
            </Tooltip>
            <Menu
              anchorEl={allMenuAnchor}
              open={Boolean(allMenuAnchor)}
              onClose={() => setAllMenuAnchor(null)}
            >
              <MenuItem onClick={() => requestAction("DELETE_FILES", true)}>
                Delete all {total.toLocaleString()} files
              </MenuItem>
              <MenuItem onClick={() => requestAction("DELETE_RECORDS", true)}>
                Remove all {total.toLocaleString()} records
              </MenuItem>
              <MenuItem onClick={() => requestAction("BLACKLIST_RECORDS", true)}>
                Blacklist all {total.toLocaleString()}
              </MenuItem>
            </Menu>
          </Stack>
        </Stack>
      </StickySelectionToolbar>

      <AssignMediaToPersonDialog
        open={assignIds !== null}
        mediaIds={assignIds ?? []}
        onClose={() => setAssignIds(null)}
        onAssigned={(person, skippedCount) => {
          const ids = assignIds ?? [];
          const assignedCount = ids.length - skippedCount;
          onAssigned?.(ids, skippedCount);
          onClearSelection();
          onFeedback(
            `Assigned ${assignedCount} item(s) to ${person.name ?? "person"}${skippedCount ? `; ${skippedCount} skipped` : ""}`,
            skippedCount ? "warning" : "success"
          );
        }}
      />

      <ConfirmDialog
        open={Boolean(pending)}
        title={copy?.title ?? ""}
        message={copy?.message ?? ""}
        confirmLabel={copy?.confirmLabel}
        onConfirm={handleConfirm}
        onClose={() => setPending(null)}
      />
    </>
  );
};

export default BulkResolveToolbar;
