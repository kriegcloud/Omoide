/**
 * React Grab (https://react-grab.com): hover an element and press Ctrl+C to
 * copy its HTML, React component and (in `vite dev`) source location for an
 * agent. Always on under the dev server; opt-in on the deployed build via
 * Alt+Shift+G. It is loaded before React mounts so it can instrument the tree,
 * which is why toggling persists the choice and reloads.
 */
export const REACT_GRAB_STORAGE_KEY = "beep.reactGrab";

export function isReactGrabEnabled(): boolean {
  if (import.meta.env.DEV) return true;
  try {
    return window.localStorage.getItem(REACT_GRAB_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export async function loadReactGrab(): Promise<void> {
  if (!isReactGrabEnabled()) return;
  try {
    await import("react-grab");
  } catch (error) {
    console.warn("[react-grab] failed to load", error);
  }
}

export function toggleReactGrab(): void {
  if (import.meta.env.DEV) return;
  try {
    const next = !isReactGrabEnabled();
    if (next) window.localStorage.setItem(REACT_GRAB_STORAGE_KEY, "1");
    else window.localStorage.removeItem(REACT_GRAB_STORAGE_KEY);
  } catch {
    return;
  }
  window.location.reload();
}
