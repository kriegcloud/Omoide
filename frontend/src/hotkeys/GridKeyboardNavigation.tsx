import { useEffect } from "react";
import { gridBindings, getTopModal, isMenuOpen } from "./keymap";
import { useHotkeys } from "./useHotkey";

interface TilePosition { top: number; left: number }

/** Find the adjacent visual cell, including column-major masonry DOM order. */
export function nextGridIndex(positions: TilePosition[], index: number, key: string): number {
  if (index < 0 || !positions.length) return index;
  const rows: number[][] = [];
  const ordered = positions.map((_, i) => i).sort((a, b) => positions[a].top - positions[b].top || positions[a].left - positions[b].left);
  ordered.forEach(i => {
    const row = rows.find(row => Math.abs(positions[row[0]].top - positions[i].top) < 4);
    if (row) row.push(i); else rows.push([i]);
  });
  rows.forEach(row => row.sort((a, b) => positions[a].left - positions[b].left));
  const visual = rows.flat();
  if (key === "ArrowLeft" || key === "ArrowRight") {
    return visual[Math.max(0, Math.min(visual.length - 1, visual.indexOf(index) + (key === "ArrowRight" ? 1 : -1)))];
  }
  const rowIndex = rows.findIndex(row => row.includes(index));
  const nextRow = rows[rowIndex + (key === "ArrowDown" ? 1 : -1)];
  return nextRow ? nextRow[Math.min(rows[rowIndex].indexOf(index), nextRow.length - 1)] : index;
}

function focusedTile(target: EventTarget | null): HTMLElement | null {
  if (!(target instanceof Element) || target.closest('button, [data-tile-control], input, textarea, select')) return null;
  return target.closest<HTMLElement>('[data-selectable-id]');
}

function tilesIn(grid: HTMLElement): HTMLElement[] {
  return Array.from(grid.querySelectorAll<HTMLElement>('[data-selectable-id]'))
    .filter(tile => tile.closest('[data-selection-grid]') === grid && tile.getClientRects().length > 0);
}

/** Face grids keep their existing roving handler; this fills the media-grid gap. */
export function GridKeyboardNavigation() {
  useHotkeys(gridBindings.filter(binding => binding.key.startsWith("Arrow")), event => {
    const tile = focusedTile(event.target);
    const grid = tile?.closest<HTMLElement>('[data-selection-grid]');
    if (!tile || !grid) return false;
    const tiles = tilesIn(grid);
    const next = nextGridIndex(tiles.map(tile => tile.getBoundingClientRect()), tiles.indexOf(tile), event.key);
    if (next < 0) return false;
    tiles.forEach((item, index) => { item.tabIndex = index === next ? 0 : -1; });
    tiles[next]?.focus();
  }, { scope: "global", when: event => !!focusedTile(event.target)?.closest('[data-selection-grid]') });

  useEffect(() => {
    let focused: HTMLElement | null = null;
    let grid: HTMLElement | null = null;
    let index = 0;
    let awaitingDismissal = false;
    const stopWaiting = () => {
      dismissalObserver.disconnect();
      awaitingDismissal = false;
    };
    const handoff = () => {
      if (!focused || focused.isConnected || !grid?.isConnected) {
        stopWaiting();
        return;
      }
      // Confirmations and menus are portalled outside the grid. If deletion
      // finishes before they close, retry when their portal is hidden/removed.
      // A closing modal may still own focus until its exit transition finishes.
      if (getTopModal() || isMenuOpen() || document.activeElement?.closest('.MuiModal-root')) {
        if (!awaitingDismissal) {
          awaitingDismissal = true;
          dismissalObserver.observe(document.body, {
            childList: true, subtree: true, attributes: true, attributeFilter: ['aria-hidden', 'class'],
          });
        }
        return;
      }
      stopWaiting();
      // A deliberate focus change owns the next action, even after removal.
      if (document.activeElement !== document.body) { focused = null; return; }
      const tiles = tilesIn(grid);
      const next = tiles[Math.min(index, tiles.length - 1)];
      if (next) { next.tabIndex = 0; next.focus(); }
    };
    const dismissalObserver = new MutationObserver(handoff);
    const observer = new MutationObserver(handoff);
    const onFocus = (event: FocusEvent) => {
      const tile = focusedTile(event.target);
      if (!tile) {
        if (awaitingDismissal && !getTopModal() && !isMenuOpen() &&
          !(event.target instanceof Element && event.target.closest('.MuiModal-root'))) {
          stopWaiting();
          focused = null;
        }
        return;
      }
      const owner = tile.closest<HTMLElement>('[data-selection-grid]');
      if (!owner) return;
      stopWaiting();
      focused = tile;
      grid = owner;
      index = tilesIn(owner).indexOf(tile);
      observer.disconnect();
      observer.observe(owner, { childList: true, subtree: true });
    };
    document.addEventListener('focusin', onFocus);
    return () => { observer.disconnect(); stopWaiting(); document.removeEventListener('focusin', onFocus); };
  }, []);
  return null;
}
