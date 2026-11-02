/**
 * Filerobot renders its tool tabs, transform tools, zoom menu and option inputs
 * as bare elements with click handlers only. Give them roles, names, focus and
 * Enter/Space activation so the editor is keyboard- and screen-reader-operable
 * (review R1-04, R2-02, R2-03).
 */
const TAB = ".FIE_tab";
const TOOL = '[class*="FIE_"][class*="-tool-button"], .FIE_crop-tool-label';
const ZOOM_TRIGGER = ".FIE_topbar-zoom-label";
const ZOOM_MENU = ".FIE_topbar-zoom-menu";
const MENU_ITEM = '[class*="SfxMenuItem-root"]';
export const EDITOR_A11Y_ATTR = "data-editor-a11y";

const title = (raw: string) => raw.replace(/-/g, " ").replace(/^\w/, c => c.toUpperCase());

function labelFor(element: Element): string {
  const text = element.querySelector(".FIE_tab-label")?.textContent || element.textContent || "";
  if (text.trim()) return text.trim();
  const tool = [...element.classList].find(name => /^FIE_.+-tool-button$/.test(name));
  return tool ? title(tool.replace(/^FIE_|-tool-button$/g, "")) : "";
}

/** "Brightness" from the nearest FIE_<name>-option / -option-wrapper ancestor. */
function optionName(element: Element, root: ParentNode): string {
  for (let node: Element | null = element; node && node !== root; node = node.parentElement) {
    const match = [...node.classList].find(name => /^FIE_.+-option(-wrapper)?$/.test(name));
    if (match) return title(match.replace(/^FIE_|-option(-wrapper)?$/g, ""));
  }
  const tab = root.querySelector?.('.FIE_tab[aria-selected="true"]');
  return tab ? labelFor(tab) : "Editor";
}

function makeControl(element: HTMLElement, role: string, label?: string) {
  if (element.getAttribute("role") !== role) element.setAttribute("role", role);
  const disabled = element.getAttribute("aria-disabled") === "true";
  const tabIndex = disabled ? -1 : 0;
  if (element.tabIndex !== tabIndex) element.tabIndex = tabIndex;
  if (!element.hasAttribute(EDITOR_A11Y_ATTR)) element.setAttribute(EDITOR_A11Y_ATTR, "");
  if (label && element.getAttribute("aria-label") !== label) element.setAttribute("aria-label", label);
}

export function enhanceEditorControls(root: ParentNode, doc: ParentNode = root) {
  root.querySelectorAll(".FIE_tabs").forEach(list => {
    if (list.getAttribute("role") !== "tablist") list.setAttribute("role", "tablist");
  });
  root.querySelectorAll<HTMLElement>(TAB).forEach(element => makeControl(element, "tab", labelFor(element)));
  root.querySelectorAll<HTMLElement>(TOOL).forEach(element => makeControl(element, "button", labelFor(element)));
  root.querySelectorAll<HTMLElement>(".FIE_tabs_toggle_btn").forEach(element => {
    if (!element.getAttribute("aria-label")) element.setAttribute("aria-label", "Toggle tool tabs");
  });
  const menu = doc.querySelector<HTMLElement>(ZOOM_MENU);
  root.querySelectorAll<HTMLElement>(ZOOM_TRIGGER).forEach(element => {
    makeControl(element, "button", element.getAttribute("title") || "Zoom");
    element.setAttribute("aria-haspopup", "menu");
    element.setAttribute("aria-expanded", menu ? "true" : "false");
  });
  if (menu) {
    if (menu.getAttribute("role") !== "menu") menu.setAttribute("role", "menu");
    if (!menu.getAttribute("aria-label")) menu.setAttribute("aria-label", "Zoom");
    menu.querySelectorAll<HTMLElement>(MENU_ITEM).forEach(item => makeControl(item, "menuitem", item.textContent?.trim() || undefined));
  }
  root.querySelectorAll<HTMLInputElement>('input[type="range"], input[type="text"], input[type="number"]').forEach(input => {
    if (input.labels?.length || input.getAttribute("aria-labelledby") || input.classList.contains("FIE_save-file-name-input")) return;
    const name = optionName(input, root);
    const label = input.type === "range" ? name : `${name} value`;
    if (input.getAttribute("aria-label") !== label) input.setAttribute("aria-label", label);
  });
}

/** Keeps controls enhanced as Filerobot re-renders (and portals its menus); returns a cleanup. */
export function observeEditorAccessibility(root: HTMLElement): () => void {
  const doc = root.ownerDocument;
  const run = () => enhanceEditorControls(root, doc);
  run();
  let queued = false;
  const schedule = () => {
    if (queued) return;
    queued = true;
    queueMicrotask(() => { queued = false; run(); });
  };
  const observer = new MutationObserver(schedule);
  observer.observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ["aria-selected", "aria-disabled"] });
  // Menus are portalled to <body>; watch only its direct children.
  const portals = new MutationObserver(schedule);
  portals.observe(doc.body, { childList: true });
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const target = event.target instanceof HTMLElement ? event.target.closest<HTMLElement>(`[${EDITOR_A11Y_ATTR}]`) : null;
    if (!target || target.getAttribute("aria-disabled") === "true") return;
    if (!root.contains(target) && !target.closest(ZOOM_MENU)) return;
    event.preventDefault();
    event.stopPropagation(); // keep app hotkeys from also acting on Enter/Space
    target.click();
  };
  doc.addEventListener("keydown", onKeyDown, true);
  return () => { observer.disconnect(); portals.disconnect(); doc.removeEventListener("keydown", onKeyDown, true); };
}
