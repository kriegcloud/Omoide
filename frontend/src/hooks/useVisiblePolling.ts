import { useEffect, useRef } from "react";

/** Poll after each response, keeping work serial and suspending hidden views. */
export function useVisiblePolling(
  request: (isCurrent: () => boolean) => Promise<unknown>,
  delay: number,
  enabled = true,
  immediate = false,
) {
  const inFlight = useRef(false);
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    let timer: number | undefined;
    const isCurrent = () => active;
    const schedule = () => {
      if (active && !document.hidden) timer = window.setTimeout(() => void poll(), delay);
    };
    const poll = async () => {
      if (!active || document.hidden) return;
      if (inFlight.current) { schedule(); return; }
      inFlight.current = true;
      try { await request(isCurrent); }
      finally { inFlight.current = false; schedule(); }
    };
    const visibility = () => {
      window.clearTimeout(timer);
      if (!document.hidden) void poll();
    };
    document.addEventListener("visibilitychange", visibility);
    if (immediate) void poll(); else schedule();
    return () => {
      active = false;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [delay, enabled, immediate, request]);
}
