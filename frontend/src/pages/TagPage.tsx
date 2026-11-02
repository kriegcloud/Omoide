import { startOperation } from "../stores/optimisticOperations";
import GalleryPage from "../components/GalleryPage";
import Gallery from "../components/Gallery";
import { useListInvalidation } from "../stores/useListStore";
import type { Tag } from "../types";
import { readCachedList } from "../stores/useListStore";
import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Typography, Box, CircularProgress } from "@mui/material";

import { useInView } from "react-intersection-observer";
import TagCard from "../components/TagCard";
import { useListStore, defaultListState } from "../stores/useListStore";
import { getTags } from "../services/tag";
import { deleteTagsBulk } from "../services/tag";
import ConfirmDialog from "../components/ConfirmDialog";
import MarqueeSelectionBox from "../components/MarqueeSelectionBox";
import { useEntitySelection } from "../hooks/useEntitySelection";
import { useGridSelection } from "../hooks/useMarqueeSelection";

export default function TagsPage() {
  const listKey = "tags-all";

  const { items, hasMore, isLoading } = useListStore(
    (state) => readCachedList<Tag>(state, listKey) || defaultListState
  );
  const fetchInitial = useListStore(state => state.fetchInitial);
  const loadMore = useListStore(state => state.loadMore);
  useListInvalidation(listKey);
  const removeItem = useListStore(state => state.removeItem);
  const removeItems = useListStore(state => state.removeItems);
  const [deleteTargets, setDeleteTargets] = useState<number[] | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const selection = useEntitySelection<number>();
  const {pruneTo} = selection;
  const { marqueeRect, onItemClick } = useGridSelection<number>({
    containerRef: gridRef,
    selecting: selection.selectionMode,
    onEnterSelection: selection.enterMode,
    onExitSelection: selection.exitMode,
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
    pruneTo(items.map((tag) => tag.id));
  }, [items, pruneTo]);

  const handleDeleteSelected = () => {
    if (!deleteTargets?.length) return;
    const ids = [...deleteTargets];
    setDeleteTargets(null);
    setIsDeleting(true);
    const operation = startOperation({
      label: "Delete selected tags",
      input: { ids }, resources: ids.map(id => `tag:${id}` as const),
      patches: ids.map(id => ({ kind: "remove" as const, key: `tag:${id}` as const, query: "tags-all" })),
      request: async (input, operationId) => {
        const result = await deleteTagsBulk(input.ids);
        removeItems(listKey, result.deleted_ids);
        return { result, operationId };
      },
      receipt: ({ result, operationId }) => ({
        operation_id: operationId, state: result.skipped_ids.length ? "partial" : "succeeded",
        applied: result.deleted_ids.map(id => `tag:${id}`),
        skipped: result.skipped_ids.map(id => ({ key: `tag:${id}`, reason: "not_found" })), failed: [], unknown: [],
      }),
    });
    void operation.done.then(() => setIsDeleting(false));
  };

  return (
    <GalleryPage title="Tags" count={items.length}>
      <Box display="flex" justifyContent="space-between" alignItems="center" mb={2}>

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
              onClick={() => setDeleteTargets(Array.from(selection.selectedIds))}
            >
              Delete Selected
            </Button>
          )}
        </Box>
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}

      <Box ref={gridRef} sx={{ position: "relative" }}><Gallery>
        {items.map((tag) => (
          <div key={tag.id}>
            {/* 1,2,3,4 per row */}
            <TagCard
              tag={tag}
              onTagDeleted={handleTagDeleted}
              selecting={selection.selectionMode}
              selected={selection.selectedIds.has(tag.id)}
              onSelectionClick={onItemClick}
            />
          </div>
        ))}
      </Gallery><MarqueeSelectionBox container={gridRef.current} rect={marqueeRect} /></Box>

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
        open={deleteTargets !== null}
        title="Delete Selected Tags"
        message={`Delete ${deleteTargets?.length ?? 0} selected tag${deleteTargets?.length === 1 ? "" : "s"}? This removes the tags from all media and people.`}
        confirmLabel="Delete"
        loading={isDeleting}
        onConfirm={handleDeleteSelected}
        onClose={() => setDeleteTargets(null)}
      />
    </GalleryPage>
  );
}
