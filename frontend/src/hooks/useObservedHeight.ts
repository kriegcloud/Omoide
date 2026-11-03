import { useLayoutEffect, type RefObject } from "react";

/**
 * Report an element's rendered height (rounded up) from ResizeObserver deliveries only.
 *
 * Deliberately no read in the mount effect: nested content can commit a placeholder
 * layout and correct it in its own layout effect, which React applies after this
 * (parent) effect has run. Gallery's first commit is a 1 px wide single column of
 * full-width tiles, so a mount-time read is several times too tall. The observer's
 * first delivery happens after that correction, in the next rendering step.
 * `key` re-subscribes (e.g. on a layout width reset) and yields a fresh delivery.
 */
export function useObservedHeight(ref: RefObject<HTMLElement | null>, onHeight: (height: number) => void, key: unknown) {
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(() => {
      const height = Math.ceil(element.getBoundingClientRect().height);
      if (height > 0) onHeight(height);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref, onHeight, key]);
}
