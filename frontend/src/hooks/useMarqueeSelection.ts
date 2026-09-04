import { useCallback, useEffect, useRef, useState } from "react";

export interface MarqueeRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

interface UseMarqueeSelectionOptions<TId extends number | string> {
  containerRef: React.RefObject<HTMLElement | null>;
  itemSelector: string;
  getId: (element: HTMLElement) => TId;
  enabled: boolean;
  selectedIds: Set<TId>;
  onSelectionChange: (ids: Set<TId>) => void;
}

const IGNORE_SELECTOR =
  "button, a[href], input, [role=menu], [data-no-marquee]";
const MOVEMENT_THRESHOLD = 6;
const AUTO_SCROLL_EDGE = 40;
const AUTO_SCROLL_STEP = 14;
// Cards whose tops fall within this band are treated as the same visual row.
const ROW_TOLERANCE_PX = 24;

export function useMarqueeSelection<TId extends number | string>({
  containerRef,
  itemSelector,
  getId,
  enabled,
  selectedIds,
  onSelectionChange,
}: UseMarqueeSelectionOptions<TId>) {
  const [marqueeRect, setMarqueeRect] = useState<MarqueeRect | null>(null);
  const anchorRef = useRef<TId | null>(null);
  const selectedIdsRef = useRef(selectedIds);
  const onSelectionChangeRef = useRef(onSelectionChange);
  const getIdRef = useRef(getId);
  const suppressClickRef = useRef(false);

  useEffect(() => {
    selectedIdsRef.current = selectedIds;
  }, [selectedIds]);
  useEffect(() => {
    onSelectionChangeRef.current = onSelectionChange;
  }, [onSelectionChange]);
  useEffect(() => {
    getIdRef.current = getId;
  }, [getId]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !enabled) {
      setMarqueeRect(null);
      return;
    }

    let pointerId: number | null = null;
    let startPageX = 0;
    let startPageY = 0;
    let clientX = 0;
    let clientY = 0;
    let active = false;
    let frame: number | null = null;
    let mode: "replace" | "add" | "remove" = "replace";
    let initialSelection = new Set<TId>();

    const updateSelection = () => {
      const currentPageX = clientX + window.scrollX;
      const currentPageY = clientY + window.scrollY;
      const rect = {
        left: Math.min(startPageX, currentPageX),
        top: Math.min(startPageY, currentPageY),
        width: Math.abs(currentPageX - startPageX),
        height: Math.abs(currentPageY - startPageY),
      };
      setMarqueeRect(rect);

      const viewportRect = {
        left: rect.left - window.scrollX,
        right: rect.left + rect.width - window.scrollX,
        top: rect.top - window.scrollY,
        bottom: rect.top + rect.height - window.scrollY,
      };
      const intersecting = new Set<TId>();
      container.querySelectorAll<HTMLElement>(itemSelector).forEach((item) => {
        const itemRect = item.getBoundingClientRect();
        if (
          itemRect.right >= viewportRect.left &&
          itemRect.left <= viewportRect.right &&
          itemRect.bottom >= viewportRect.top &&
          itemRect.top <= viewportRect.bottom
        ) {
          intersecting.add(getIdRef.current(item));
        }
      });

      if (mode === "add") {
        onSelectionChangeRef.current(
          new Set([...initialSelection, ...intersecting]),
        );
      } else if (mode === "remove") {
        onSelectionChangeRef.current(
          new Set(Array.from(initialSelection).filter((id) => !intersecting.has(id))),
        );
      } else {
        onSelectionChangeRef.current(intersecting);
      }
    };

    const tick = () => {
      frame = null;
      if (!active) return;
      if (clientY < AUTO_SCROLL_EDGE) {
        window.scrollBy(0, -AUTO_SCROLL_STEP);
      } else if (clientY > window.innerHeight - AUTO_SCROLL_EDGE) {
        window.scrollBy(0, AUTO_SCROLL_STEP);
      }
      updateSelection();
      frame = window.requestAnimationFrame(tick);
    };

    const stop = () => {
      pointerId = null;
      if (frame !== null) window.cancelAnimationFrame(frame);
      frame = null;
      if (active) {
        suppressClickRef.current = true;
        window.setTimeout(() => {
          suppressClickRef.current = false;
        }, 0);
      }
      active = false;
      setMarqueeRect(null);
      container.style.removeProperty("user-select");
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      window.removeEventListener("pointercancel", handlePointerUp);
    };

    const handlePointerMove = (event: PointerEvent) => {
      if (event.pointerId !== pointerId) return;
      clientX = event.clientX;
      clientY = event.clientY;
      if (!active) {
        const distance = Math.hypot(
          event.pageX - startPageX,
          event.pageY - startPageY,
        );
        if (distance < MOVEMENT_THRESHOLD) return;
        active = true;
        container.style.userSelect = "none";
        frame = window.requestAnimationFrame(tick);
      }
      event.preventDefault();
      updateSelection();
    };

    const handlePointerUp = (event: PointerEvent) => {
      if (event.pointerId !== pointerId) return;
      stop();
    };

    const handlePointerDown = (event: PointerEvent) => {
      if (
        event.button !== 0 ||
        event.pointerType === "touch" ||
        pointerId !== null
      ) {
        return;
      }
      const target = event.target;
      if (!(target instanceof Element)) return;
      const selectableItem = target.closest(itemSelector);
      const interactive = target.closest(IGNORE_SELECTOR);
      const isItemLink =
        interactive?.matches("a[href]") &&
        selectableItem instanceof HTMLElement &&
        (interactive === selectableItem || interactive.parentElement === selectableItem);
      if (interactive && !isItemLink) return;

      pointerId = event.pointerId;
      startPageX = event.pageX;
      startPageY = event.pageY;
      clientX = event.clientX;
      clientY = event.clientY;
      initialSelection = new Set(selectedIdsRef.current);
      mode = event.altKey
        ? "remove"
        : event.ctrlKey || event.metaKey
          ? "add"
          : "replace";
      window.addEventListener("pointermove", handlePointerMove, {
        passive: false,
      });
      window.addEventListener("pointerup", handlePointerUp);
      window.addEventListener("pointercancel", handlePointerUp);
    };

    container.addEventListener("pointerdown", handlePointerDown);
    return () => {
      container.removeEventListener("pointerdown", handlePointerDown);
      stop();
    };
  }, [containerRef, enabled, itemSelector]);

  const onItemClick = useCallback(
    (id: TId, event: React.MouseEvent) => {
      if (!enabled) return;
      event.preventDefault();
      event.stopPropagation();
      if (suppressClickRef.current) return;

      const container = containerRef.current;
      if (event.shiftKey && anchorRef.current !== null && container) {
        // Order by visual position (rows, then columns) rather than DOM order:
        // masonry grids render column-major, so DOM order would make a range
        // across one visual row span whole columns.
        const orderedIds = Array.from(
          container.querySelectorAll<HTMLElement>(itemSelector),
          (item) => {
            const rect = item.getBoundingClientRect();
            return {
              id: getIdRef.current(item),
              top: Math.round(rect.top / ROW_TOLERANCE_PX),
              left: rect.left,
            };
          },
        )
          .sort((a, b) => a.top - b.top || a.left - b.left)
          .map((entry) => entry.id);
        const anchorIndex = orderedIds.indexOf(anchorRef.current);
        const clickedIndex = orderedIds.indexOf(id);
        if (anchorIndex >= 0 && clickedIndex >= 0) {
          const [start, end] =
            anchorIndex < clickedIndex
              ? [anchorIndex, clickedIndex]
              : [clickedIndex, anchorIndex];
          onSelectionChangeRef.current(new Set(orderedIds.slice(start, end + 1)));
          return;
        }
      }

      const next = new Set(selectedIdsRef.current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      anchorRef.current = id;
      onSelectionChangeRef.current(next);
    },
    [containerRef, enabled, itemSelector],
  );

  return { marqueeRect, onItemClick };
}
