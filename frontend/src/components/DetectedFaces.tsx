import { SelectionHotkeyDialogs } from "../hotkeys/SelectionHotkeyDialogs";
import { useUndo } from "../context/UndoContext";
import { useHotkey, useHotkeys } from "../hotkeys/useHotkey";
import React, { useCallback, useState, useEffect, useMemo, useRef } from "react";
import {
  Chip,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Paper,
  Stack,
  TextField,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import VideoLibraryIcon from "@mui/icons-material/VideoLibrary";
import ViewModuleIcon from "@mui/icons-material/ViewModule";
import { FaceRead, Person } from "../types";
import FaceCard from "./FaceCard";
import FaceMediaGroup from "./FaceMediaGroup";
import MarqueeSelectionBox from "./MarqueeSelectionBox";
import { useGridSelection, type SelectionClickEvent } from "../hooks/useMarqueeSelection";
import ConfirmDialog from "./ConfirmDialog";
import { useFaceSelection } from "../hooks/useFaceSelection";
import PersonPicker from "./PersonPicker";
import config from "../config";
import { useRovingGridFocus } from "../hooks/useRovingGridFocus";

interface DetectedFacesProps {
  isProcessing: boolean;
  faces: FaceRead[];
  title: string;
  onDelete: (faceIds: number[]) => void | Promise<void>;
  onDetach: (faceIds: number[]) => void | Promise<void>;
  onAssign: (faceIds: number[], personId: number) => void | Promise<void>;
  onCreateMultiple?: (faceIds: number[], name?: string) => Promise<Person | void>;
  personId?: number;
  assignToCurrentPerson?: boolean;

  profileFaceId?: number;
  onSetProfile?: (faceId: number) => void | Promise<void>;

  onLoadMore?: () => void;
  hasMore?: boolean;
  isLoadingMore?: boolean;

  disableInternalScroll?: boolean;

  // Pinned faces jumped from timeline
  pinnedFaces?: FaceRead[];
  onClearPinned?: () => void;
}

export default function DetectedFaces({
  isProcessing,
  faces,
  title,
  onDelete,
  onDetach,
  onAssign,
  personId,
  assignToCurrentPerson = false,
  profileFaceId,
  onSetProfile,
  onLoadMore,
  hasMore,
  isLoadingMore,
  onCreateMultiple,
  disableInternalScroll = false,
  pinnedFaces,
  onClearPinned,
}: DetectedFacesProps) {
  const theme = useTheme();
  const {
    selectedFaceIds,
    onSelectAll,
    onClearSelection,
    setSelectedFaceIds,
  } = useFaceSelection();

  const [expandedGroupIds, setExpandedGroupIds] = useState<Set<number>>(new Set());

  const toggleGroupExpand = useCallback((mediaId: number) => {
    setExpandedGroupIds((prev) => {
      const next = new Set(prev);
      if (next.has(mediaId)) next.delete(mediaId);
      else next.add(mediaId);
      return next;
    });
  }, []);

  const [isAssignDialogOpen, setIsAssignDialogOpen] = useState(false);
  const [assignTargetPerson, setAssignTargetPerson] = useState<Person | null>(null);
  const [openCreateDialog, setOpenCreateDialog] = useState(false);
  const [detachIds, setDetachIds] = useState<number[] | null>(null);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [newPersonName, setNewPersonName] = useState("");

  const { refreshVisible } = useUndo();
  const canMutate = !config.PRESENTATION_MODE;
  const [groupByVideo, setGroupByVideo] = useState(config.GROUP_FACES_BY_VIDEO);
  const isAnythingSelected = selectedFaceIds.length > 0;

  // Group faces by media_id; preserve iteration order
  const groupedItems = useMemo(() => {
    const map = new Map<number, FaceRead[]>();
    for (const face of faces) {
      const existing = map.get(face.media_id);
      if (existing) {
        existing.push(face);
      } else {
        map.set(face.media_id, [face]);
      }
    }
    return Array.from(map.entries()).map(([mediaId, fs]) => ({ mediaId, faces: fs }));
  }, [faces]);

  const containerRef = useRef<HTMLDivElement>(null);
  const reviewBusy = useRef(false);
  const [isReviewing, setIsReviewing] = useState(false);
  useRovingGridFocus(containerRef);
  const clickedGroupRef = useRef<number[] | null>(null);
  const selectedIdSet = useMemo(() => new Set(selectedFaceIds), [selectedFaceIds]);
  const handleSelectionChange = useCallback((ids: Set<number>) => {
    const next = new Set(ids);
    // Collapsed cards are one visual target containing several real face IDs.
    // Complete endpoint groups in a range and preserve group checkbox toggles.
    const groups = clickedGroupRef.current
      ? [clickedGroupRef.current]
      : groupByVideo
        ? groupedItems.filter((group) => !expandedGroupIds.has(group.mediaId))
            .map((group) => group.faces.map((face) => face.id))
        : [];
    for (const group of groups) {
      const added = group.some((id) => next.has(id) && !selectedIdSet.has(id));
      const removed = group.some((id) => !next.has(id) && selectedIdSet.has(id));
      if (added) group.forEach((id) => next.add(id));
      else if (removed) group.forEach((id) => next.delete(id));
    }
    setSelectedFaceIds(Array.from(next));
  }, [groupByVideo, groupedItems, expandedGroupIds, selectedIdSet, setSelectedFaceIds]);
  const { marqueeRect, onItemClick } = useGridSelection({
    containerRef,
    itemSelector: "[data-selectable-id]:not([data-selection-group])",
    selectedIds: selectedIdSet,
    onSelectionChange: handleSelectionChange,
    selecting: isAnythingSelected,
    disabled: !canMutate,
  });
  const handleGroupSelectionClick = (faceIds: number[], event: SelectionClickEvent) => {
    // A partially selected group adds its remaining faces on a normal toggle.
    const targetId = faceIds.find((id) => !selectedIdSet.has(id)) ?? faceIds[0];
    clickedGroupRef.current = faceIds;
    try {
      return onItemClick(targetId, event);
    } finally {
      clickedGroupRef.current = null;
    }
  };

  const [lastCardNode, setLastCardNode] = useState<HTMLDivElement | null>(
    null,
  );
  const lastCardRef = useCallback((node: HTMLDivElement | null) => {
    setLastCardNode(node);
  }, []);

  useEffect(() => {
    if (!lastCardNode || !hasMore || !onLoadMore || isLoadingMore) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          onLoadMore();
        }
      },
      { threshold: 0.1, rootMargin: "0px 0px 100px 0px" },
    );
    observer.observe(lastCardNode);
    return () => {
      observer.disconnect();
    };
  }, [lastCardNode, hasMore, isLoadingMore, onLoadMore]);

  useEffect(() => {
    onClearSelection();
  }, [personId]);

  useEffect(() => {
    if (!canMutate) {
      setIsAssignDialogOpen(false);
      setOpenCreateDialog(false);
      onClearSelection();
    }
  }, [canMutate, onClearSelection]);

  const handleDetach = useCallback(async (faceIds = selectedFaceIds) => {
    if (!faceIds.length || !canMutate || isProcessing || reviewBusy.current) return;
    reviewBusy.current = true;
    setIsReviewing(true);
    try {
      await onDetach([...faceIds]);
      onClearSelection();
    } catch (error) {
      console.error("Failed to detach faces:", error);
    } finally {
      reviewBusy.current = false;
      setIsReviewing(false);
    }
  }, [selectedFaceIds, canMutate, isProcessing, onDetach, onClearSelection]);

  const handleSetProfile = useCallback(async (faceId: number) => {
    if (!onSetProfile || !canMutate || isProcessing || reviewBusy.current) return;
    reviewBusy.current = true;
    setIsReviewing(true);
    try {
      await onSetProfile(faceId);
    } catch (error) {
      console.error("Failed to set profile face:", error);
    } finally {
      reviewBusy.current = false;
      setIsReviewing(false);
    }
  }, [onSetProfile, canMutate, isProcessing]);

  useHotkeys([
    { key: "d", description: "Detach selected or focused faces…", destructive: true },
    { key: "p", description: "Set selected or focused face as profile", destructive: true },
  ], (event) => {
      const key = event.key.toLowerCase();
      const target = event.target;
      if (!(target instanceof HTMLElement)) return false;
      // A collapsed video group is not a single face. Space selects its faces;
      // expand it (or use the flat grid) before acting on a focused single face.
      const tile = target.closest<HTMLElement>("[data-roving-tile]:not([data-selection-group])");
      const focusedId = tile && containerRef.current?.contains(tile)
        ? Number(tile.dataset.selectableId) : undefined;
      const availableIds = new Set([...faces, ...(pinnedFaces ?? [])].map((face) => face.id));
      const ids = selectedFaceIds.length
        ? selectedFaceIds.filter((id) => availableIds.has(id))
        : focusedId !== undefined && availableIds.has(focusedId) ? [focusedId] : [];
      if (!ids.length || (key === "p" && (!onSetProfile || ids.length !== 1))) return false;
      event.preventDefault();
      event.stopPropagation();
      if (key === "d") setDetachIds(ids);
      else void handleSetProfile(ids[0]);
  }, { scope: "page", enabled: !!personId && canMutate && !isProcessing && !isReviewing && !isAssignDialogOpen && !openCreateDialog && !confirmDeleteOpen });
  useHotkey({ key: "a" }, () => handleAssignClick(), { scope: "page", enabled: canMutate && selectedFaceIds.length > 0 && !isProcessing, description: "Assign selected faces…" });
  useHotkey({ key: "Escape" }, onClearSelection, { scope: "page", enabled: selectedFaceIds.length > 0, description: "Clear face selection" });

  if (faces.length === 0 && !isLoadingMore && !hasMore && title === "Detected Faces") {
    return null;
  }
  if (faces.length === 0 && isLoadingMore && onLoadMore) {
    return null;
  }

  const handleAssign = async (faceIds: number[], assignedToPersonId: number) => {
    if (!canMutate) return;
    try {
      await onAssign(faceIds, assignedToPersonId);
      onClearSelection();
    } catch (error) {
      console.error("Failed to assign faces:", error);
    }
  };

  const handleConfirmDelete = async () => {
    if (!onDelete || selectedFaceIds.length === 0 || !canMutate) return;
    const faceIds = [...selectedFaceIds];
    try {
      await onDelete(faceIds);
      onClearSelection();
      setConfirmDeleteOpen(false);
    } catch (error) {
      console.error("Failed to delete faces:", error);
      setConfirmDeleteOpen(false);
    }
  };

  const handleCloseAssignDialog = () => {
    setIsAssignDialogOpen(false);
    setAssignTargetPerson(null);
  };

  const handleConfirmAssign = async () => {
    if (!assignTargetPerson || assignTargetPerson.id === personId || !canMutate || isProcessing) return;
    await handleAssign(selectedFaceIds, assignTargetPerson.id);
    handleCloseAssignDialog();
  };

  const handleAssignClick = () => {
    if (!canMutate) return;
    if (personId && assignToCurrentPerson) {
      handleAssign(selectedFaceIds, personId);
    } else {
      setIsAssignDialogOpen(true);
    }
  };

  const scrollContainerSx = !disableInternalScroll
    ? {
        maxHeight: "400px",
        overflowY: "auto",
        pr: 1,
        "&::-webkit-scrollbar": { width: "8px" },
        "&::-webkit-scrollbar-track": { background: theme.palette.background.default },
        "&::-webkit-scrollbar-thumb": {
          backgroundColor: theme.palette.divider,
          borderRadius: "4px",
        },
        "&::-webkit-scrollbar-thumb:hover": { background: theme.palette.text.secondary },
      }
    : {};

  const faceItems: React.ReactNode[] = groupByVideo
    ? groupedItems.flatMap((item, groupIndex) => {
        const isLastGroup = groupIndex === groupedItems.length - 1;
        if (item.faces.length > 1) {
          const isExpanded = expandedGroupIds.has(item.mediaId);
          return [
            <div
              key={`group-${item.mediaId}`}
              ref={!disableInternalScroll && isLastGroup && !isExpanded ? lastCardRef : null}
            >
              <FaceMediaGroup
                faces={item.faces}
                selectedFaceIds={selectedFaceIds}
                selecting={isAnythingSelected}
                expanded={isExpanded}
                onSelectionClick={handleGroupSelectionClick}
                canMutate={canMutate}
                onToggleExpand={() => toggleGroupExpand(item.mediaId)}
              />
            </div>,
            isExpanded && (
              <Box
                key={`expanded-${item.mediaId}`}
                ref={!disableInternalScroll && isLastGroup ? lastCardRef : null}
                sx={{
                  flexBasis: "100%",
                  display: "flex",
                  flexWrap: "wrap",
                  gap: 1,
                  p: 1,
                  borderRadius: 1,
                  bgcolor: "action.hover",
                }}
              >
                {item.faces.map((face) => (
                  <FaceCard
                    keyboardReview
                    key={face.id}
                    face={face}
                    isProfile={face.id === profileFaceId}
                    onSetProfile={canMutate && onSetProfile ? handleSetProfile : undefined}
                    selected={canMutate && selectedFaceIds.includes(face.id)}
                    selecting={isAnythingSelected}
                    onSelectionClick={canMutate ? onItemClick : undefined}
                  />
                ))}
              </Box>
            ),
          ].filter(Boolean) as React.ReactNode[];
        }
        return [
          <div
            key={item.faces[0].id}
            ref={!disableInternalScroll && isLastGroup ? lastCardRef : null}
          >
            <FaceCard
              keyboardReview
              face={item.faces[0]}
              isProfile={item.faces[0].id === profileFaceId}
              onSetProfile={canMutate && onSetProfile ? handleSetProfile : undefined}
              selected={canMutate && selectedFaceIds.includes(item.faces[0].id)}
              selecting={isAnythingSelected}
              onSelectionClick={canMutate ? onItemClick : undefined}
            />
          </div>,
        ];
      })
    : faces.map((face, index) => {
        const isLast = index === faces.length - 1;
        return (
          <div
            key={face.id}
            ref={!disableInternalScroll && isLast ? lastCardRef : null}
          >
            <FaceCard
              keyboardReview
              face={face}
              isProfile={face.id === profileFaceId}
              onSetProfile={canMutate && onSetProfile ? handleSetProfile : undefined}
              selected={canMutate && selectedFaceIds.includes(face.id)}
              selecting={isAnythingSelected}
              onSelectionClick={canMutate ? onItemClick : undefined}
            />
          </div>
        );
      });

  return (
    <Paper variant="outlined" sx={{ p: 2, my: 4 }}>
      <SelectionHotkeyDialogs includeDelete enabled={!isProcessing && !isReviewing}
        mediaIds={[...new Set([...faces, ...(pinnedFaces ?? [])].filter(face => selectedFaceIds.includes(face.id)).map(face => face.media_id))]}
        onProcessed={async () => { onClearSelection(); await refreshVisible(); }} />
      <Box sx={{ mb: 1 }}>
        <Stack direction="row" alignItems="center" sx={{ mb: 1 }}>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>
            {title}
          </Typography>
          <Tooltip title={groupByVideo ? "Show as flat grid" : "Group by video"}>
            <IconButton size="small" onClick={() => setGroupByVideo((v) => !v)}>
              {groupByVideo ? <ViewModuleIcon fontSize="small" /> : <VideoLibraryIcon fontSize="small" />}
            </IconButton>
          </Tooltip>
        </Stack>
        {isAnythingSelected && canMutate && (
          <Stack direction="row" spacing={1} alignItems="center">
            <Button size="small" onClick={onClearSelection}>
              {selectedFaceIds.length} selected · {new Set([...faces, ...(pinnedFaces ?? [])].map((face) => face.id)).size} loaded
            </Button>
            {hasMore && <Chip size="small" label="Load more to select the rest" />}
            <Box sx={{ flexGrow: 1 }} />
            <Button
              variant="contained"
              size="small"
              disabled={isProcessing}
              onClick={handleAssignClick}
            >
              Assign
            </Button>
            {onCreateMultiple && (
              <Button
                variant="contained"
                size="small"
                disabled={isProcessing}
                onClick={() => setOpenCreateDialog(true)}
              >
                Create New
              </Button>
            )}
            {onDetach && (
              <Button
                variant="outlined"
                color="secondary"
                size="small"
                disabled={isProcessing || isReviewing}
                onClick={() => void handleDetach()}
              >
                Detach
              </Button>
            )}
            {onDelete && (
              <Button
                variant="outlined"
                color="error"
                size="small"
                disabled={isProcessing}
                onClick={() => setConfirmDeleteOpen(true)}
              >
                Delete
              </Button>
            )}
            {onSetProfile && (
              <Button
                variant="contained"
                size="small"
                disabled={isProcessing || isReviewing || selectedFaceIds.length !== 1}
                onClick={() => void handleSetProfile(selectedFaceIds[0])}
              >
                Set as Profile
              </Button>
            )}
            {isProcessing && <CircularProgress size={20} />}
          </Stack>
        )}
        <Button
          size="small"
          onClick={() => onSelectAll(faces)}
          disabled={!canMutate}
        >
          {selectedFaceIds.length < faces.length ? "Select All" : "Select None"}
        </Button>
        {personId && canMutate && (
          <Typography variant="caption" color="text.secondary" display="block">
            D: detach selected faces or the focused face
            {onSetProfile ? " · P: set as profile (one face only)" : ""}.
            {" "}Expand grouped tiles to focus a single face.
          </Typography>
        )}
      </Box>

      {/* Create-person dialog */}
      <Dialog open={canMutate && openCreateDialog} onClose={() => setOpenCreateDialog(false)}>
        <DialogTitle>Create New Person</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            margin="dense"
            label="Person Name"
            type="text"
            fullWidth
            variant="standard"
            value={newPersonName}
            onChange={(e) => setNewPersonName(e.target.value)}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpenCreateDialog(false)}>Cancel</Button>
          <Button
            onClick={async () => {
              if (onCreateMultiple && canMutate) {
                try {
                  await onCreateMultiple(selectedFaceIds, newPersonName);
                  setOpenCreateDialog(false);
                  setSelectedFaceIds([]);
                  setNewPersonName("");
                } catch (error) {
                  console.error("Failed to create person:", error);
                }
              }
            }}
          >
            Create
          </Button>
        </DialogActions>
      </Dialog>

      <ConfirmDialog open={canMutate && detachIds !== null} title="Detach faces?"
        message={`Detach ${detachIds?.length ?? 0} face(s) from this person?`} confirmLabel="Detach" confirmColor="warning"
        loading={isReviewing} onClose={() => setDetachIds(null)} onConfirm={async () => {
          if (!detachIds) return;
          await handleDetach(detachIds);
          setDetachIds(null);
        }} />
      <ConfirmDialog
        open={canMutate && confirmDeleteOpen}
        title="Delete Faces"
        message={`Delete ${selectedFaceIds.length} selected face${selectedFaceIds.length === 1 ? "" : "s"}? This cannot be undone.`}
        confirmLabel="Delete"
        loading={isProcessing}
        onConfirm={handleConfirmDelete}
        onClose={() => setConfirmDeleteOpen(false)}
      />

      {/* Assign-to-person dialog */}
      <Dialog
        open={canMutate && isAssignDialogOpen}
        onClose={handleCloseAssignDialog}
        fullWidth
        maxWidth="xs"
      >
        <DialogTitle>Assign to Person</DialogTitle>
        <DialogContent>
          {canMutate && isAssignDialogOpen && (
            <PersonPicker
              autoFocus
              label="Search for a person"
              excludeIds={personId === undefined ? [] : [personId]}
              selectedId={assignTargetPerson?.id}
              disabled={isProcessing}
              onSelect={setAssignTargetPerson}
            />
          )}
          {assignTargetPerson && (
            <Typography sx={{ mt: 2 }}>
              Selected: {assignTargetPerson.name || `Person ${assignTargetPerson.id}`}
            </Typography>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={handleCloseAssignDialog}>Cancel</Button>
          <Button onClick={handleConfirmAssign} disabled={!assignTargetPerson || isProcessing}>
            Assign
          </Button>
        </DialogActions>
      </Dialog>

      <Box ref={containerRef} sx={{ position: "relative" }}>
        {/* Pinned faces (jumped from timeline) */}
        {pinnedFaces && pinnedFaces.length > 0 && (
          <Paper
            variant="outlined"
            sx={{ p: 2, mb: 2, borderColor: "primary.main", borderWidth: 2 }}
          >
            <Box
              sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 1 }}
            >
              <Typography variant="body2" color="primary">
                Jumped from timeline — {pinnedFaces.length} face
                {pinnedFaces.length !== 1 ? "s" : ""} from this photo
              </Typography>
              <Button size="small" onClick={onClearPinned}>
                Clear
              </Button>
            </Box>
            <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1 }}>
              {pinnedFaces.map((face) => (
                <FaceCard
                  keyboardReview
                  key={face.id}
                  face={face}
                  isProfile={face.id === profileFaceId}
                  onSetProfile={canMutate && onSetProfile ? handleSetProfile : undefined}
                  selected={canMutate && selectedFaceIds.includes(face.id)}
                  selecting={isAnythingSelected}
                  onSelectionClick={canMutate ? onItemClick : undefined}
                />
              ))}
            </Box>
          </Paper>
        )}

        {/* Faces grid — grouped by media */}
        <Box sx={scrollContainerSx}>
          {faces.length === 0 && !isLoadingMore ? (
            <Typography sx={{ textAlign: "center", p: 4, color: "text.secondary" }}>
              No faces to display.
            </Typography>
          ) : (
            <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1, alignItems: "flex-start" }}>
              {faceItems}
            </Box>
          )}
          {isLoadingMore && (
            <Box sx={{ display: "flex", justifyContent: "center", p: 2 }}>
              <CircularProgress size={24} />
            </Box>
          )}
        </Box>
        <MarqueeSelectionBox container={containerRef.current} rect={marqueeRect} />
      </Box>
    </Paper>
  );
}
