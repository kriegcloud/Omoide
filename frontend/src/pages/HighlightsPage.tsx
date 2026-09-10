import { mutationBus } from "../stores/mutationBus";
import { ListRevision } from "../stores/listReconciliation";
import ListState from "../components/ListState";
import { useUndoRefresh } from "../context/UndoContext";
import React, { useCallback, useEffect, useRef, useState } from "react";
import Masonry from "react-masonry-css";
import {
  Box,
  Chip,
  Container,
  Typography,
} from "@mui/material";
import StarIcon from "@mui/icons-material/Star";
import MediaCard from "../components/MediaCard";
import { getHighlights, getHighlightYears } from "../services/features";
import { HighlightYear, Media } from "../types";
import { useSelection } from "../context/SelectionContext";
import { useGridSelection } from "../hooks/useMarqueeSelection";
import MarqueeSelectionBox from "../components/MarqueeSelectionBox";

const breakpointColumnsObj = {
  default: 5,
  1600: 4,
  1200: 3,
  900: 3,
  600: 2,
};

export default function HighlightsPage() {
  const [years, setYears] = useState<HighlightYear[]>([]);
  const [year, setYear] = useState<number | null>(null);
  const [items, setItems] = useState<Media[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const generationRef = useRef(0);
  const revisionRef = useRef(new ListRevision());
  const refreshLocalRef = useRef<(prefix: string) => void>(() => {});
  const [retryYears, setRetryYears] = useState(0);
  useEffect(() => mutationBus.subscribe(event => {
    if (event.type === "list:invalidate") { refreshLocalRef.current(event.prefix); return; }
    if (event.type === "media:deleted") revisionRef.current.remove(event.ids);
    else if (event.type === "media:updated" || event.type === "media:moved") revisionRef.current.patch(event.items);
    else return;
    setItems(previous => revisionRef.current.reconcile(previous, 0, false));
  }), []);
  const loadHighlights = useCallback(async () => {
    const generation = ++generationRef.current;
    const revision = revisionRef.current.revision;
    if (year === null) return;
    setIsLoading(true);
    setError(null);
    try {
      const data = await getHighlights(year);
      if (generation === generationRef.current) setItems(revisionRef.current.reconcile(data, revision, true));
    } catch (err) {
      if (generation === generationRef.current)
        setError(err instanceof Error ? err.message : "Failed to load highlights");
    } finally {
      if (generation === generationRef.current) setIsLoading(false);
    }
  }, [year]);
  useUndoRefresh(`highlights:${year}`, loadHighlights);
  refreshLocalRef.current = prefix => {
    if (!(prefix === "" || `highlights:${year}`.startsWith(prefix) || `highlights-${year}`.startsWith(prefix))) return;
    if (year === null) setRetryYears(value => value + 1);
    else void loadHighlights();
  };
  const gridRef = useRef<HTMLDivElement>(null);
  const { isSelecting, selectedIds, setSelected, beginSelecting, clear } = useSelection();
  const { marqueeRect, onItemClick } = useGridSelection<number>({
    listKey: `highlights:${year}`,
    loadedCount: items.length,
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
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    getHighlightYears()
      .then((data) => {
        if (cancelled) return;
        setYears(data);
        if (data.length > 0) setYear(data[0].year);
        else setIsLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load years");
        setIsLoading(false);
      });
    return () => { cancelled = true; };
  }, [retryYears]);

  useEffect(() => {
    setItems([]);
    void loadHighlights();
    return () => { generationRef.current += 1; };
  }, [loadHighlights]);

  return (
    <Container maxWidth="xl" sx={{ minHeight: "100vh", py: 4 }}>
      <Box display="flex" alignItems="center" gap={1} mb={2}>
        <StarIcon color="primary" />
        <Typography variant="h5" fontWeight={700}>
          Highlights
        </Typography>
      </Box>
      <Typography variant="body2" color="text.secondary" mb={2}>
        The best of each year, picked from favorites, faces, sharpness and
        views.
      </Typography>

      <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap", mb: 3 }}>
        {years.map((y) => (
          <Chip
            key={y.year}
            label={y.year}
            clickable
            color={y.year === year ? "primary" : "default"}
            onClick={() => setYear(y.year)}
          />
        ))}
      </Box>

      <ListState loading={isLoading && items.length === 0} error={error}
        empty={!isLoading && items.length === 0} emptyMessage="No highlights yet. Favorite some media to help choose highlights."
        onRetry={() => { if (year === null) setRetryYears(value => value + 1); else void loadHighlights(); }} />
      {items.length > 0 && (
        <Box ref={gridRef} sx={{ position: "relative" }}>
          <Masonry
            breakpointCols={breakpointColumnsObj}
            className="my-masonry-grid"
            columnClassName="my-masonry-grid_column"
          >
            {items.map((media) => (
              <div key={media.id}>
                <MediaCard
                  media={media}
                  mediaListKey={`highlights-${year}`}
                  onSelectionClick={onItemClick}
                />
              </div>
            ))}
          </Masonry>
          <MarqueeSelectionBox container={gridRef.current} rect={marqueeRect} />
        </Box>
      )}
    </Container>
  );
}
