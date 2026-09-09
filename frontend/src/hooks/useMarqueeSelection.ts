import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { useSelection, useSelectionList } from "../context/SelectionContext";

export interface MarqueeRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface SelectionClickEvent {
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
  preventDefault: () => void;
  stopPropagation: () => void;
}

interface UseGridSelectionOptions<TId extends number | string> {
  listKey?: string;
  loadedCount?: number;
  hasMore?: boolean;
  containerRef: React.RefObject<HTMLElement | null>;
  itemSelector?: string;
  getId?: (element: HTMLElement) => TId;
  selecting: boolean;
  onEnterSelection?: () => void;
  onExitSelection?: () => void;
  allowPlainDragOnItems?: boolean;
  disabled?: boolean;
  selectedIds: Set<TId>;
  onSelectionChange: (ids: Set<TId>) => void;
}

const IGNORE_SELECTOR =
  "button, a[href], input, textarea, select, [contenteditable]:not([contenteditable=false]), [role=menu], [data-no-marquee]";
const MOVEMENT_THRESHOLD = 6;
const AUTO_SCROLL_EDGE = 40;
const AUTO_SCROLL_STEP = 14;
// Cards whose tops fall within this band are treated as the same visual row.
const ROW_TOLERANCE_PX = 24;

const defaultGetId = (element: HTMLElement) => Number(element.dataset.selectableId);

export function useGridSelection<TId extends number | string = number>({
  listKey,
  loadedCount,
  hasMore = false,
  containerRef,
  itemSelector = "[data-selectable-id]",
  getId = defaultGetId as (element: HTMLElement) => TId,
  selecting,
  onEnterSelection,
  onExitSelection,
  allowPlainDragOnItems = true,
  disabled = false,
  selectedIds,
  onSelectionChange,
}: UseGridSelectionOptions<TId>) {
  const [marqueeRect, setMarqueeRect] = useState<MarqueeRect | null>(null);
  const anchorRef = useRef<TId | null>(null);
  const selectedIdsRef = useRef(selectedIds);
  const onSelectionChangeRef = useRef(onSelectionChange);
  const getIdRef = useRef(getId);
  const suppressClickRef = useRef(false);
  const selectingRef = useRef(selecting);
  const onEnterSelectionRef = useRef(onEnterSelection);
  const cancelMarqueeRef = useRef<(() => void) | null>(null);
  const [container, setContainer] = useState<HTMLElement | null>(null);
  const [mountedCount, setMountedCount] = useState(0);
  const selection = useSelection();
  const { pathname } = useLocation();
  const ownsMediaSelection = onSelectionChange === selection.setSelected;
  useSelectionList(ownsMediaSelection ? listKey ?? pathname : null, loadedCount ?? mountedCount, hasMore);

  useEffect(() => {
    if (!container || !ownsMediaSelection || loadedCount !== undefined) return;
    const update = () => setMountedCount(new Set(Array.from(
      container.querySelectorAll<HTMLElement>(itemSelector), getIdRef.current,
    )).size);
    update();
    const observer = new MutationObserver(update);
    observer.observe(container, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [container, itemSelector, ownsMediaSelection, loadedCount]);

  // Some grids mount only after their asynchronous results arrive.
  useEffect(() => {
    setContainer(containerRef.current);
  });
  useEffect(() => {
    selectingRef.current = selecting;
    onEnterSelectionRef.current = onEnterSelection;
  }, [selecting, onEnterSelection]);
  useEffect(() => {
    if (!selecting) {
      anchorRef.current = null;
      if (cancelMarqueeRef.current) cancelMarqueeRef.current();
    }
  }, [selecting]);

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
    if (!container || disabled) {
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
    let enteredSelection = false;
    const initialUserSelect = container.style.userSelect;

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

      const next = mode === "add"
        ? new Set([...initialSelection, ...intersecting])
        : mode === "remove"
          ? new Set(Array.from(initialSelection).filter((id) => !intersecting.has(id)))
          : intersecting;
      if (next.size > 0 && !selectingRef.current && !enteredSelection) {
        enteredSelection = true;
        if (onEnterSelectionRef.current) onEnterSelectionRef.current();
      }
      selectedIdsRef.current = next;
      onSelectionChangeRef.current(next);
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
      const capturedPointer = pointerId;
      pointerId = null;
      if (capturedPointer !== null && container.hasPointerCapture(capturedPointer)) {
        container.releasePointerCapture(capturedPointer);
      }
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
      container.style.userSelect = initialUserSelect;
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      window.removeEventListener("pointercancel", handlePointerUp);
    };

    const handlePointerMove = (event: PointerEvent) => {
      if (event.pointerId !== pointerId) return;
      clientX = event.clientX;
      clientY = event.clientY;
      mode = event.altKey ? "remove" : event.ctrlKey || event.metaKey ? "add" : "replace";
      if (!active) {
        const distance = Math.hypot(
          event.pageX - startPageX,
          event.pageY - startPageY,
        );
        if (distance < MOVEMENT_THRESHOLD) return;
        active = true;
        container.setPointerCapture(event.pointerId);
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
        selectableItem.contains(interactive);
      if (interactive && !isItemLink) return;
      const modified = event.ctrlKey || event.metaKey || event.shiftKey || event.altKey;
      if (selectableItem && !selectingRef.current && !modified && !allowPlainDragOnItems) return;
      if (modified) event.preventDefault();

      enteredSelection = false;
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

    // Cancel native drag initiation for an eligible marquee, including links/images.
    const handleDragStart = (event: DragEvent) => {
      if (pointerId !== null) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    const handleClick = (event: MouseEvent) => {
      if (suppressClickRef.current) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    cancelMarqueeRef.current = stop;
    container.addEventListener("pointerdown", handlePointerDown);
    container.addEventListener("dragstart", handleDragStart, true);
    container.addEventListener("click", handleClick, true);
    return () => {
      container.removeEventListener("pointerdown", handlePointerDown);
      container.removeEventListener("dragstart", handleDragStart, true);
      container.removeEventListener("click", handleClick, true);
      cancelMarqueeRef.current = null;
      stop();
    };
  }, [container, disabled, itemSelector, allowPlainDragOnItems]);

  useEffect(() => {
    if (!container || disabled) return;
    const handleSelectAll = (event: KeyboardEvent) => {
      if (event.defaultPrevented || !(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "a") return;
      const target = event.target;
      if (target instanceof HTMLElement && (target.closest("input, textarea, select, [contenteditable]:not([contenteditable=false])") || target.isContentEditable)) return;
      // A modal or another focused grid owns its keyboard events.
      if (target instanceof Element && target.closest("[role=dialog], [role=menu]")) return;
      const focusedGrid = target instanceof Element ? target.closest("[data-selection-grid]") : null;
      if (focusedGrid && focusedGrid !== container) return;
      if (container.getClientRects().length === 0) return;
      const next = new Set(Array.from(container.querySelectorAll<HTMLElement>(itemSelector), getIdRef.current));
      if (!next.size) return;
      event.preventDefault();
      cancelMarqueeRef.current?.();
      onEnterSelectionRef.current?.();
      selectedIdsRef.current = next;
      onSelectionChangeRef.current(next);
    };
    container.setAttribute("data-selection-grid", "");
    window.addEventListener("keydown", handleSelectAll);
    return () => {
      container.removeAttribute("data-selection-grid");
      window.removeEventListener("keydown", handleSelectAll);
    };
  }, [container, disabled, itemSelector]);

  useEffect(() => {
    if (!selecting || disabled) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      const target = event.target;
      if (target instanceof HTMLElement && (
        target.closest("input, textarea, select") || target.isContentEditable
      )) return;
      // A page can have multiple grids sharing one selection store.
      event.preventDefault();
      if (cancelMarqueeRef.current) cancelMarqueeRef.current();
      selectedIdsRef.current = new Set<TId>();
      onSelectionChangeRef.current(new Set<TId>());
      if (onExitSelection) onExitSelection();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [selecting, disabled, onExitSelection]);

  const isSelectionGesture = useCallback(
    (event: SelectionClickEvent) => !disabled && (
      selecting || event.ctrlKey || event.metaKey || event.shiftKey
    ),
    [selecting, disabled],
  );

  const onItemClick = useCallback(
    (id: TId, event: SelectionClickEvent): boolean => {
      if (disabled) return false;
      if (suppressClickRef.current) return true;
      if (!isSelectionGesture(event)) return false;
      if (!selecting && onEnterSelectionRef.current) onEnterSelectionRef.current();

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
          const next = new Set([...selectedIdsRef.current, ...orderedIds.slice(start, end + 1)]);
          selectedIdsRef.current = next;
          onSelectionChangeRef.current(next);
          return true;
        }
      }

      const next = new Set(selectedIdsRef.current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      anchorRef.current = id;
      selectedIdsRef.current = next;
      onSelectionChangeRef.current(next);
      return true;
    },
    [containerRef, disabled, selecting, isSelectionGesture, itemSelector],
  );

  return { marqueeRect, onItemClick, isSelectionGesture };
}

type UseMarqueeSelectionOptions<TId extends number | string> = Omit<
  UseGridSelectionOptions<TId>, "selecting"
> & { enabled: boolean };

/** @deprecated Use useGridSelection with selecting instead of enabled. */
export function useMarqueeSelection<TId extends number | string = number>({
  enabled,
  ...options
}: UseMarqueeSelectionOptions<TId>) {
  const selection = useGridSelection({ ...options, selecting: enabled });
  // Legacy cards expect the hook itself to cancel navigation.
  const onItemClick = (id: TId, event: SelectionClickEvent) => {
    const consumed = selection.onItemClick(id, event);
    if (consumed) {
      event.preventDefault();
      event.stopPropagation();
    }
    return consumed;
  };
  return { ...selection, onItemClick };
}
