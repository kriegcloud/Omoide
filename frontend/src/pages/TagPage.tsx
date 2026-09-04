import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Container, Typography, Box, CircularProgress } from "@mui/material";
import Grid from "@mui/material/Grid";

import { useInView } from "react-intersection-observer";
import TagCard from "../components/TagCard";
import { useListStore, defaultListState } from "../stores/useListStore";
import { getTags } from "../services/tag";
import { deleteTagsBulk } from "../services/tag";
import ConfirmDialog from "../components/ConfirmDialog";
import MarqueeSelectionBox from "../components/MarqueeSelectionBox";
import { useEntitySelection } from "../hooks/useEntitySelection";
import { useMarqueeSelection } from "../hooks/useMarqueeSelection";

export default function TagsPage() {
  const listKey = "tags-all";

  const { items, hasMore, isLoading } = useListStore(
    (state) => state.lists[listKey] || defaultListState
  );
  const { fetchInitial, loadMore, removeItem, removeItems } = useListStore();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const selection = useEntitySelection<number>();
  const { marqueeRect, onItemClick } = useMarqueeSelection<number>({
    containerRef: gridRef,
    itemSelector: "[data-selectable-id]",
    getId: (element) => Number(element.dataset.selectableId),
    enabled: selection.selectionMode,
    selectedIds: selection.selectedIds,
    onSelectionChange: selection.setSelected,
  });

  const { ref: loaderRef, inView } = useInView({ threshold: 0.5 });

  useEffect(() => {
    fetchInitial(listKey, () => getTags(null));
  }, [fetchInitial, listKey]);

  useEffect(() => {
    if (inView && hasMore && !isLoading) {
      loadMore(listKey, (cursor) => getTags(cursor));
    }
  }, [inView, hasMore, isLoading, loadMore, listKey]);
  const handleTagDeleted = useCallback(
    (deletedTagId: number) => {
      removeItem(listKey, deletedTagId);
    },
    [removeItem, listKey]
  );
  useEffect(() => {
    selection.pruneTo(items.map((tag) => tag.id));
  }, [items, selection.pruneTo]);

  const handleDeleteSelected = async () => {
    setIsDeleting(true);
    try {
      const result = await deleteTagsBulk(Array.from(selection.selectedIds));
      removeItems(listKey, result.deleted_ids);
      selection.toggleMode();
      setDeleteOpen(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to delete tags");
    } finally {
      setIsDeleting(false);
    }
  };
  return (
    <Container maxWidth="lg" sx={{ pt: 4, pb: 6 }}>
      <Box display="flex" justifyContent="space-between" alignItems="center" mb={2}>
        <Typography variant="h4">Tags</Typography>
        <Box display="flex" gap={1} alignItems="center">
          {selection.selectionMode && (
            <Typography variant="body2" color="text.secondary">
              {selection.selectedIds.size} selected
            </Typography>
          )}
          <Button variant="outlined" size="small" onClick={selection.toggleMode}>
            {selection.selectionMode ? "Cancel Selection" : "Select Tags"}
          </Button>
          {selection.selectionMode && (
            <Button
              variant="contained"
              color="error"
              size="small"
              disabled={selection.selectedIds.size === 0 || isDeleting}
              onClick={() => setDeleteOpen(true)}
            >
              Delete Selected
            </Button>
          )}
        </Box>
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}

      <Grid ref={gridRef} container spacing={4} sx={{ position: "relative" }}>
        {items.map((tag) => (
          <Grid key={tag.id} size={{ xs: 12, sm: 6, md: 4, lg: 3 }}>
            {/* 1,2,3,4 per row */}
            <TagCard
              tag={tag}
              onTagDeleted={handleTagDeleted}
              selectable={selection.selectionMode}
              selected={selection.selectedIds.has(tag.id)}
              onSelectionClick={onItemClick}
            />
          </Grid>
        ))}
        <MarqueeSelectionBox container={gridRef.current} rect={marqueeRect} />
      </Grid>

      {isLoading && (
        <Box textAlign="center" py={4}>
          <CircularProgress color="secondary" />
        </Box>
      )}

      {!isLoading && hasMore && (
        <Box
          ref={loaderRef}
          textAlign="center"
          py={2}
          sx={{ color: "text.secondary" }}
        >
          Scroll to load more…
        </Box>
      )}
      <ConfirmDialog
        open={deleteOpen}
        title="Delete Selected Tags"
        message={`Delete ${selection.selectedIds.size} selected tag${selection.selectedIds.size === 1 ? "" : "s"}? This removes the tags from all media and people.`}
        confirmLabel="Delete"
        loading={isDeleting}
        onConfirm={handleDeleteSelected}
        onClose={() => setDeleteOpen(false)}
      />
    </Container>
  );
}
