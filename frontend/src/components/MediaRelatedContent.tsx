import ListState from "./ListState";
import { mutationBus } from "../stores/mutationBus";
import { ListRevision } from "../stores/listReconciliation";
import { useUndoRefresh } from "../context/UndoContext";
import React, { useEffect, useState, useMemo, useRef } from "react";
import { Box, Typography } from "@mui/material";
import { Media } from "../types";
import MediaCard from "./MediaCard";
import { getSimilarMedia } from "../services/media";
import { useSelection } from "../context/SelectionContext";
import { useGridSelection } from "../hooks/useMarqueeSelection";
import MarqueeSelectionBox from "./MarqueeSelectionBox";

export default function SimilarContent({ mediaId }: { mediaId: number }) {
  const [similar, setSimilar] = useState<Media[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const generation = useRef(0);
  const revision = useRef(new ListRevision());
  useUndoRefresh(`similar:${mediaId}`, async () => {
    const request = ++generation.current;
    const started = revision.current.revision;
    const incoming = await getSimilarMedia(mediaId);
    if (request === generation.current) { setSimilar(revision.current.reconcile(incoming, started, true)); setError(null); }
  });
  useEffect(() => mutationBus.subscribe(event => {
    if (event.type === "media:deleted") {
      revision.current.remove(event.ids);
      setSimilar(previous => previous.filter(item => !event.ids.includes(item.id)));
    } else if (event.type === "media:updated") {
      revision.current.patch(event.items);
      const patches = new Map(event.items.map(item => [item.id, item]));
      setSimilar(previous => previous.map(item => ({ ...item, ...patches.get(item.id) })));
    } else if (event.type === "list:invalidate" && event.prefix === "") setRetry(value => value + 1);
  }), []);
  const similarIds = useMemo(() => similar.map((item) => item.id), [similar]);
  const gridRef = useRef<HTMLDivElement>(null);
  const { isSelecting, selectedIds, setSelected, beginSelecting, clear } = useSelection();
  const { marqueeRect, onItemClick } = useGridSelection<number>({
    listKey: `similar:${mediaId}`,
    loadedCount: similar.length,
    containerRef: gridRef,
    itemSelector: "[data-media-card]",
    getId: (element) => Number(element.dataset.selectableId),
    selecting: isSelecting,
    allowPlainDragOnItems: false,
    onEnterSelection: beginSelecting,
    onExitSelection: clear,
    selectedIds,
    onSelectionChange: setSelected,
  });

  useEffect(() => {
    if (!mediaId) return;
    const request = ++generation.current;
    const started = revision.current.revision;
    const controller = new AbortController();
    const { signal } = controller;

    // Reset stale results from the previously shown media
    setSimilar([]);
    setIsLoading(true);
    setError(null);
    getSimilarMedia(mediaId, signal)
      .then((items) => {
        if (!signal.aborted && request === generation.current) setSimilar(revision.current.reconcile(items, started, true));
      })
      .catch((err) => {
        // When the fetch is aborted, it throws an error. We can safely ignore it.
        if (!signal.aborted && err.name !== "AbortError") {
          if (request === generation.current) setError(err instanceof Error ? err.message : "Failed to load similar media");
        }
      })
      .finally(() => {
        if (!signal.aborted && request === generation.current) setIsLoading(false);
      });
    return () => {
      generation.current += 1;
      controller.abort();
    };
  }, [mediaId, retry]);

  if (isLoading || error || similar.length === 0) {
    return <ListState loading={isLoading} error={error} empty={similar.length === 0} emptyMessage="No similar content found." onRetry={() => setRetry(value => value + 1)} />;
  }

  return (
    <Box>
      <Typography variant="h6" gutterBottom>
        Similar Content
      </Typography>

      <Box
        ref={gridRef}
        sx={{
          columnCount: { xs: 2, sm: 2, md: 3 },
          columnGap: (theme) => theme.spacing(2),
          position: "relative",
        }}
      >
        {similar.map((item) => (
          <Box
            key={item.id}
            sx={{
              breakInside: "avoid",
              mb: 2,
            }}
          >
            <MediaCard
              media={item}
              navigationContext={{ ids: similarIds }}
              onSelectionClick={onItemClick}
            />
          </Box>
        ))}
        <MarqueeSelectionBox container={gridRef.current} rect={marqueeRect} />
      </Box>
    </Box>
  );
}
