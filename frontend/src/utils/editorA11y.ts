/**
 * Filerobot renders its tool tabs and transform tools as bare div/li/span with
 * click handlers only. Give them roles, focus and Enter/Space activation so the
 * editor is keyboard- and screen-reader-operable (review R1-04).
 */
const TAB = ".FIE_tab";
const TOOL = '[class*="FIE_"][class*="-tool-button"], .FIE_crop-tool-label';
export const EDITOR_A11Y_ATTR = "data-editor-a11y";

function labelFor(element: Element): string {
  const text = element.querySelector(".FIE_tab-label")?.textContent || element.textContent || "";
  if (text.trim()) return text.trim();
  const tool = [...element.classList].find(name => /^FIE_.+-tool-button$/.test(name));
  return tool ? tool.replace(/^FIE_|-tool-button$/g, "").replace(/-/g, " ") : "";
}

export function enhanceEditorControls(root: ParentNode) {
  root.querySelectorAll(".FIE_tabs").forEach(list => {
    if (list.getAttribute("role") !== "tablist") list.setAttribute("role", "tablist");
  });
  root.querySelectorAll<HTMLElement>(`${TAB}, ${TOOL}`).forEach(element => {
    const isTab = element.matches(TAB);
    const role = isTab ? "tab" : "button";
    if (element.getAttribute("role") !== role) element.setAttribute("role", role);
    if (element.tabIndex < 0 || !element.hasAttribute("tabindex")) element.tabIndex = 0;
    if (!element.hasAttribute(EDITOR_A11Y_ATTR)) element.setAttribute(EDITOR_A11Y_ATTR, "");
    const label = labelFor(element);
    if (label && element.getAttribute("aria-label") !== label) element.setAttribute("aria-label", label);
  });
}

/** Keeps controls enhanced as Filerobot re-renders; returns a cleanup. */
export function observeEditorAccessibility(root: HTMLElement): () => void {
  enhanceEditorControls(root);
  let queued = false;
  const observer = new MutationObserver(() => {
    if (queued) return;
    queued = true;
    queueMicrotask(() => { queued = false; enhanceEditorControls(root); });
  });
  observer.observe(root, { childList: true, subtree: true });
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const target = event.target instanceof HTMLElement ? event.target.closest<HTMLElement>(`[${EDITOR_A11Y_ATTR}]`) : null;
    if (!target || !root.contains(target)) return;
    event.preventDefault();
    target.click();
  };
  root.addEventListener("keydown", onKeyDown);
  return () => { observer.disconnect(); root.removeEventListener("keydown", onKeyDown); };
}
