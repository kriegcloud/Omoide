import { createContext, useContext, useEffect, useRef } from "react";

/** Viewport point at which a tile's actions menu should open. */
export interface TileMenuPoint {
  left: number;
  top: number;
}

export type TileMenuHandler = (point: TileMenuPoint) => void;

/**
 * One slot per tile: the actions menu rendered inside a SelectableTileFrame
 * registers how to open itself at a point; the frame calls it on right-click.
 * A tile with no registered menu keeps the browser's native context menu.
 */
export interface TileContextMenuRegistry {
  register(handler: TileMenuHandler): () => void;
  current(): TileMenuHandler | null;
}

export function createTileContextMenuRegistry(): TileContextMenuRegistry {
  let handler: TileMenuHandler | null = null;
  return {
    register(next) {
      handler = next;
      return () => {
        if (handler === next) handler = null;
      };
    },
    current: () => handler,
  };
}

export interface TileContextMenuEventLike {
  clientX: number;
  clientY: number;
  shiftKey: boolean;
  target: EventTarget | null;
}

const NATIVE_TARGETS = "input, textarea, select, [contenteditable]";

/**
 * Where to open the tile menu for a contextmenu event, or null to leave the
 * native browser menu alone. Shift+right-click always gets the native menu
 * (copy image, open in new tab); text inputs keep theirs. A keyboard-invoked
 * contextmenu (Menu key / Shift+F10) reports 0,0, so it opens at the tile corner.
 */
export function tileMenuPoint(
  event: TileContextMenuEventLike,
  tileRect: { left: number; top: number; width: number },
  hasHandler: boolean,
): TileMenuPoint | null {
  if (!hasHandler || event.shiftKey) return null;
  const target = event.target as { closest?: (selector: string) => unknown } | null;
  if (target && typeof target.closest === "function" && target.closest(NATIVE_TARGETS)) return null;
  if (event.clientX === 0 && event.clientY === 0) {
    return { left: tileRect.left + tileRect.width - 8, top: tileRect.top + 8 };
  }
  return { left: event.clientX, top: event.clientY };
}

export const TileContextMenuContext = createContext<TileContextMenuRegistry | null>(null);

/** Called by a menu component to open itself when its tile is right-clicked. */
export function useTileContextMenu(handler: TileMenuHandler): void {
  const registry = useContext(TileContextMenuContext);
  const latest = useRef(handler);
  latest.current = handler;
  useEffect(() => {
    if (!registry) return undefined;
    return registry.register((point) => latest.current(point));
  }, [registry]);
}
