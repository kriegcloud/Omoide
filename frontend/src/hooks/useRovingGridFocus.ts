import { useHotkeys } from "../hotkeys/useHotkey";
import { gridBindings, isHotkeyAllowed } from "../hotkeys/keymap";
import { useEffect, useRef, useState, type RefObject } from "react";

/** One tab stop per face grid; measure rows again after responsive layout changes. */
export function useRovingGridFocus(containerRef: RefObject<HTMLElement | null>) {
  useHotkeys(gridBindings.filter(binding => binding.key.startsWith("Arrow")), () => {}, {
    scope: "page", local: true,
    when: event => !!containerRef.current?.contains(event.target as Node),
  });
  const [container, setContainer] = useState<HTMLElement | null>(null);
  const focused = useRef<HTMLElement | null>(null);
  useEffect(() => { setContainer(containerRef.current); });
  useEffect(() => {
    if (!container) return;
    const tiles = () => Array.from(container.querySelectorAll<HTMLElement>("[data-roving-tile]"));
    const sync = () => {
      const items = tiles();
      if (!focused.current || !items.includes(focused.current)) focused.current = items[0] ?? null;
      items.forEach((item) => { item.tabIndex = item === focused.current ? 0 : -1; });
    };
    const onFocus = (event: FocusEvent) => {
      const tile = event.target instanceof Element ? event.target.closest<HTMLElement>("[data-roving-tile]") : null;
      if (tile && container.contains(tile)) { focused.current = tile; sync(); }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (!isHotkeyAllowed(event) || !event.key.startsWith("Arrow") || event.altKey || event.ctrlKey || event.metaKey) return;
      const items = tiles();
      const index = items.indexOf(event.target as HTMLElement);
      if (index < 0) return; // Nested buttons and inputs retain their own keys.
      event.preventDefault();
      event.stopPropagation();
      let next = index;
      if (event.key === "ArrowLeft") next -= 1;
      if (event.key === "ArrowRight") next += 1;
      if (event.key === "ArrowUp" || event.key === "ArrowDown") {
        // Wrapped DetectedFaces tiles have different offsetParents. Normalize their
        // tops into the container's coordinates before finding the adjacent row.
        const origin = container.getBoundingClientRect().top;
        const tops = items.map((item) => item.offsetParent === container
          ? item.offsetTop : item.getBoundingClientRect().top - origin + container.scrollTop);
        const rows: number[][] = [];
        items.forEach((_, i) => {
          const row = rows.find((row) => Math.abs(tops[row[0]] - tops[i]) < 2);
          if (row) row.push(i); else rows.push([i]);
        });
        rows.sort((a, b) => tops[a[0]] - tops[b[0]]);
        const rowIndex = rows.findIndex((row) => row.includes(index));
        const column = rows[rowIndex].indexOf(index);
        const row = rows[rowIndex + (event.key === "ArrowDown" ? 1 : -1)];
        if (row) next = row[Math.min(column, row.length - 1)];
      }
      focused.current = items[Math.max(0, Math.min(next, items.length - 1))];
      sync();
      focused.current?.focus();
    };
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(container, { childList: true, subtree: true });
    container.addEventListener("focusin", onFocus);
    container.addEventListener("keydown", onKeyDown);
    return () => {
      observer.disconnect();
      container.removeEventListener("focusin", onFocus);
      container.removeEventListener("keydown", onKeyDown);
    };
  }, [container]);
}
