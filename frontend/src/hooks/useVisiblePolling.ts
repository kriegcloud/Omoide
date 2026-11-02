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
    let ownsRequest = false;
    const isCurrent = () => active;
    const schedule = () => {
      window.clearTimeout(timer);
      if (active && !document.hidden) timer = window.setTimeout(() => void poll(), delay);
    };
    const poll = async () => {
      if (!active || document.hidden) return;
      if (inFlight.current) {
        // Only a replacement effect needs a timer: the current request schedules
        // its own next poll in finally. Visibility events must not add a second.
        if (!ownsRequest) schedule();
        return;
      }
      inFlight.current = true;
      ownsRequest = true;
      try { await request(isCurrent); }
      finally { inFlight.current = false; ownsRequest = false; schedule(); }
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
