import { useHotkeyRegistry } from "../hotkeys/useHotkey";
import { selectAllBindings } from "../hotkeys/keymap";
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
  /** Lets a tile restore the whole gesture when its next click opens it. */
  registerUndo?: (restore: () => void) => void;
}

interface UseGridSelectionOptions<TId extends number | string> {
  listKey?: string;
  loadedCount?: number;
  /** Complete visual order for virtualized grids; selection survives unmounts. */
  orderedIds?: TId[];
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
const AUTO_SCROLL_EDGE = 80;
const AUTO_SCROLL_STEP = 48;
// Cards whose tops fall within this band are treated as the same visual row.
const ROW_TOLERANCE_PX = 24;

const defaultGetId = (element: HTMLElement) => Number(element.dataset.selectableId);

/** Quadratic easing gives precise movement at the edge and speed near its end. */
export function marqueeScrollVelocity(position: number, top: number, bottom: number): number {
  const edge = Math.min(AUTO_SCROLL_EDGE, (bottom - top) / 2);
  if (edge <= 0) return 0;
  const depth = position < top + edge
    ? -Math.min(1, Math.max(0, (top + edge - position) / edge))
    : Math.min(1, Math.max(0, (position - bottom + edge) / edge));
  return Math.sign(depth) * depth * depth * AUTO_SCROLL_STEP;
}

function scrollableAncestor(container: HTMLElement): HTMLElement | null {
  // The grid itself may be the scrollport (e.g. an internally scrolling face grid).
  for (let element: HTMLElement | null = container;
    element && element !== document.body && element !== document.documentElement;
    element = element.parentElement) {
    if (/(auto|scroll|overlay)/.test(window.getComputedStyle(element).overflowY) &&
      element.scrollHeight > element.clientHeight) return element;
  }
  return null;
}

function sameMembership<TId>(left: Set<TId>, right: Set<TId>): boolean {
  return left.size === right.size && Array.from(left).every((id) => right.has(id));
}

export function useGridSelection<TId extends number | string = number>({
  listKey,
  loadedCount,
  orderedIds: loadedIds,
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
  const registerHotkey = useHotkeyRegistry();
  const [marqueeRect, setMarqueeRect] = useState<MarqueeRect | null>(null);
  const anchorRef = useRef<TId | null>(null);
  const clickUndoRef = useRef<{ token: symbol; ids: Set<TId>; anchor: TId | null } | null>(null);
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
    let mode: "add" | "remove" = "add";
    let initialSelection = new Set<TId>();
    let enteredSelection = false;
    let scrollTarget: HTMLElement | null = null;
    let geometryDirty = true;
    let tiles: { id: TId; left: number; right: number; top: number; bottom: number }[] = [];
    let scrollBounds = { top: 0, bottom: window.innerHeight };
    let resizeObserver: ResizeObserver | null = null;
    let mutationObserver: MutationObserver | null = null;
    const initialUserSelect = container.style.userSelect;

    const scrollOffset = () => ({
      x: window.scrollX + (scrollTarget?.scrollLeft ?? 0),
      y: window.scrollY + (scrollTarget?.scrollTop ?? 0),
    });
    const measureBounds = () => {
      const rect = scrollTarget?.getBoundingClientRect();
      const top = (rect?.top ?? 0) + (scrollTarget?.clientTop ?? 0);
      scrollBounds = {
        top: Math.max(0, top),
        bottom: Math.min(window.innerHeight, scrollTarget ? top + scrollTarget.clientHeight : window.innerHeight),
      };
    };
    const invalidateGeometry = () => { geometryDirty = true; };
    const invalidateLayout = () => {
      // Offscreen virtual rows cannot be remeasured after a responsive reflow.
      tiles = [];
      invalidateGeometry();
    };
    const measureTiles = () => {
      const offset = scrollOffset();
      resizeObserver?.disconnect();
      resizeObserver?.observe(container);
      if (scrollTarget && scrollTarget !== container) resizeObserver?.observe(scrollTarget);
      const items = Array.from(container.querySelectorAll<HTMLElement>(itemSelector))
        .filter((item) => !scrollTarget || scrollTarget.contains(item));
      // Retain positions sampled earlier in this drag when a virtual scrollport
      // unmounts rows. They still participate as the rectangle grows or shrinks.
      // The cache is discarded at drag end and on layout resize.
      const measured = new Map(scrollTarget ? tiles.map((tile) => [tile.id, tile]) : []);
      for (const item of items) {
        const rect = item.getBoundingClientRect();
        resizeObserver?.observe(item);
        const id = getIdRef.current(item);
        measured.set(id, {
          id,
          left: rect.left + offset.x, right: rect.right + offset.x,
          top: rect.top + offset.y, bottom: rect.bottom + offset.y,
        });
      }
      tiles = Array.from(measured.values());
      measureBounds();
      geometryDirty = false;
    };
    const updateSelection = () => {
      if (geometryDirty) measureTiles();
      const offset = scrollOffset();
      const currentPageX = clientX + offset.x;
      const currentPageY = clientY + offset.y;
      const rect = {
        left: Math.min(startPageX, currentPageX),
        top: Math.min(startPageY, currentPageY),
        width: Math.abs(currentPageX - startPageX),
        height: Math.abs(currentPageY - startPageY),
      };
      // Keep the public rectangle in page coordinates: the overlay can live
      // inside the scrollport or outside it (the virtualized duplicates list).
      const overlayRect = {
        ...rect,
        left: rect.left - (scrollTarget?.scrollLeft ?? 0),
        top: rect.top - (scrollTarget?.scrollTop ?? 0),
      };
      setMarqueeRect((previous) => previous && previous.left === overlayRect.left &&
        previous.top === overlayRect.top && previous.width === overlayRect.width &&
        previous.height === overlayRect.height ? previous : overlayRect);
      const intersecting = new Set<TId>();
      for (const item of tiles) {
        if (
          item.right >= rect.left &&
          item.left <= rect.left + rect.width &&
          item.bottom >= rect.top &&
          item.top <= rect.top + rect.height
        ) {
          intersecting.add(item.id);
        }
      }

      const next = mode === "add"
        ? new Set([...initialSelection, ...intersecting])
        : new Set(Array.from(initialSelection).filter((id) => !intersecting.has(id)));
      if (next.size > 0 && !selectingRef.current && !enteredSelection) {
        enteredSelection = true;
        if (onEnterSelectionRef.current) onEnterSelectionRef.current();
      }
      if (!sameMembership(next, selectedIdsRef.current)) {
        selectedIdsRef.current = next;
        onSelectionChangeRef.current(next);
      }
    };

    const tick = () => {
      frame = null;
      if (!active) return;
      if (geometryDirty) measureTiles();
      const top = marqueeScrollVelocity(clientY, scrollBounds.top, scrollBounds.bottom);
      if (top !== 0) {
        (scrollTarget ?? window).scrollBy({ top, behavior: "instant" });
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
      resizeObserver?.disconnect();
      mutationObserver?.disconnect();
      resizeObserver = null;
      mutationObserver = null;
      tiles = [];
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
      window.removeEventListener("blur", stop);
      window.removeEventListener("resize", invalidateLayout);
      window.removeEventListener("scroll", measureBounds);
    };

    const handlePointerMove = (event: PointerEvent) => {
      if (event.pointerId !== pointerId) return;
      clientX = event.clientX;
      clientY = event.clientY;
      mode = event.altKey ? "remove" : "add";
      if (!active) {
        const offset = scrollOffset();
        const distance = Math.hypot(
          clientX + offset.x - startPageX,
          clientY + offset.y - startPageY,
        );
        if (distance < MOVEMENT_THRESHOLD) return;
        active = true;
        container.setPointerCapture(event.pointerId);
        container.style.userSelect = "none";
        // Ignore initial resize notifications and selection-chrome mutations;
        // only actual geometry changes should invalidate the cached tile rects.
        const sizes = new WeakMap<Element, string>();
        resizeObserver = new ResizeObserver((entries) => {
          for (const entry of entries) {
            const size = `${entry.contentRect.width}:${entry.contentRect.height}`;
            const previous = sizes.get(entry.target);
            if (previous !== undefined && previous !== size) invalidateLayout();
            sizes.set(entry.target, size);
          }
        });
        mutationObserver = new MutationObserver((records) => {
          if (records.some((record) => record.type === "attributes"
            ? record.target instanceof Element &&
              (record.target.matches(itemSelector) || record.target.querySelector(itemSelector))
            : [...record.addedNodes, ...record.removedNodes].some((node) =>
              node instanceof Element && (node.matches(itemSelector) || node.querySelector(itemSelector))))) {
            invalidateGeometry();
          }
        });
        mutationObserver.observe(container, {
          childList: true, subtree: true, attributes: true,
          attributeFilter: ["data-selectable-id", "style", "class"],
        });
        geometryDirty = true;
        window.addEventListener("resize", invalidateLayout);
        window.addEventListener("scroll", measureBounds);
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
      // Start at the pointer target as face grids can have an internal
      // scrollport below the selection container (and a separate pinned section).
      scrollTarget = scrollableAncestor(target instanceof HTMLElement ? target : target.parentElement ?? container);
      const offset = scrollOffset();
      startPageX = event.clientX + offset.x;
      startPageY = event.clientY + offset.y;
      clientX = event.clientX;
      clientY = event.clientY;
      // Always union/subtract against this snapshot, so shrinking the rectangle
      // preserves previous drags but releases ids added only by this drag.
      initialSelection = new Set(selectedIdsRef.current);
      mode = event.altKey ? "remove" : "add";
      window.addEventListener("pointermove", handlePointerMove, {
        passive: false,
      });
      window.addEventListener("pointerup", handlePointerUp);
      window.addEventListener("pointercancel", handlePointerUp);
      window.addEventListener("blur", stop);
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
      if (event.defaultPrevented || !(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "a") return false;
      const target = event.target;
      if (target instanceof HTMLElement && (target.closest("input, textarea, select, [contenteditable]:not([contenteditable=false])") || target.isContentEditable)) return false;
      // A modal or another focused grid owns its keyboard events.
      if (target instanceof Element && target.closest("[role=dialog], [role=menu]")) return false;
      const focusedGrid = target instanceof Element ? target.closest("[data-selection-grid]") : null;
      if (focusedGrid && focusedGrid !== container) return false;
      if (container.getClientRects().length === 0) return false;
      const allLoaded = new Set(loadedIds ?? Array.from(container.querySelectorAll<HTMLElement>(itemSelector), getIdRef.current));
      if (!allLoaded.size) return false;
      const next = Array.from(allLoaded).every((id) => selectedIdsRef.current.has(id))
        ? new Set<TId>()
        : allLoaded;
      event.preventDefault();
      cancelMarqueeRef.current?.();
      onEnterSelectionRef.current?.();
      selectedIdsRef.current = next;
      onSelectionChangeRef.current(next);
    };
    container.setAttribute("data-selection-grid", "");
    const unregister = registerHotkey(() => ({
      bindings: selectAllBindings,
      handler: handleSelectAll,
      options: { scope: "page", when: () => container.getClientRects().length > 0 },
    }));
    return () => {
      container.removeAttribute("data-selection-grid");
      unregister();
    };
  }, [container, disabled, itemSelector, registerHotkey, loadedIds]);

  useEffect(() => {
    if (!selecting || disabled) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      const target = event.target;
      if (target instanceof HTMLElement && (
        target.closest("input, textarea, select") || target.isContentEditable
      )) return;
      if (ownsMediaSelection) {
        cancelMarqueeRef.current?.();
        return false;
      }
      // A page can have multiple grids sharing one selection store.
      event.preventDefault();
      if (cancelMarqueeRef.current) cancelMarqueeRef.current();
      selectedIdsRef.current = new Set<TId>();
      onSelectionChangeRef.current(new Set<TId>());
      if (onExitSelection) onExitSelection();
    };
    return registerHotkey(() => ({
      bindings: [{ key: "Escape" }], handler: handleKeyDown,
      options: { scope: "page", description: "Clear selection" },
    }));
  }, [selecting, disabled, onExitSelection, ownsMediaSelection, registerHotkey]);

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
      clickUndoRef.current = null;
      if (event.registerUndo) {
        const token = Symbol();
        // Keep one snapshot per grid, rather than retaining an increasingly
        // large selection snapshot in every previously clicked tile.
        clickUndoRef.current = { token, ids: new Set(selectedIdsRef.current), anchor: anchorRef.current };
        event.registerUndo(() => {
          const previous = clickUndoRef.current;
          if (previous?.token !== token) return;
          clickUndoRef.current = null;
          anchorRef.current = previous.anchor;
          selectedIdsRef.current = previous.ids;
          onSelectionChangeRef.current(new Set(previous.ids));
        });
      }
      if (!selecting && onEnterSelectionRef.current) onEnterSelectionRef.current();

      const container = containerRef.current;
      if (event.shiftKey && anchorRef.current !== null && container) {
        // Order by visual position (rows, then columns) rather than DOM order:
        // masonry grids render column-major, so DOM order would make a range
        // across one visual row span whole columns.
        const orderedIds = loadedIds ?? Array.from(
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
    [containerRef, disabled, selecting, isSelectionGesture, itemSelector, loadedIds],
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
