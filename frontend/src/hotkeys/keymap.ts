export type HotkeyScope = "global" | "page" | "dialog";
export interface HotkeyBinding {
  key: string;
  ctrl?: boolean;
  shift?: boolean;
  meta?: boolean;
  alt?: boolean;
  description?: string;
  scope?: HotkeyScope;
  destructive?: boolean;
}

export const scopePriority: Record<HotkeyScope, number> = { global: 0, page: 1, dialog: 2 };
export const editableSelector = 'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [role="slider"], [role="combobox"]';
export const isEditableTarget = (target: EventTarget | null): boolean =>
  target instanceof Element && (!!target.closest(editableSelector) || (target instanceof HTMLElement && target.isContentEditable));

export function matchesBinding(binding: HotkeyBinding, event: KeyboardEvent): boolean {
  const shiftedSymbol = binding.key === "?" || binding.key === "+";
  return binding.key.toLowerCase() === event.key.toLowerCase() &&
    !!binding.ctrl === event.ctrlKey && !!binding.meta === event.metaKey &&
    !!binding.alt === event.altKey &&
    (binding.shift === undefined && shiftedSymbol || !!binding.shift === event.shiftKey);
}

export function getTopModal(): HTMLElement | null {
  // A menu marks its owning dialog aria-hidden for screen readers. That dialog
  // still owns the shortcut scope; .MuiModal-hidden identifies closed modals.
  const modals = Array.from(document.querySelectorAll<HTMLElement>('.MuiModal-root:not(.MuiModal-hidden):not(.MuiMenu-root)'));
  return modals.at(-1) ?? null;
}

export function isMenuOpen(): boolean {
  return !!document.querySelector('.MuiMenu-root:not(.MuiModal-hidden):not([aria-hidden="true"]) [role="menu"]');
}

export function formatBinding(binding: HotkeyBinding): string {
  const label: Record<string, string> = { " ": "Space", Escape: "Esc", Delete: "Del", ArrowLeft: "←", ArrowRight: "→", ArrowUp: "↑", ArrowDown: "↓" };
  return [binding.ctrl && "Ctrl", binding.meta && "⌘", binding.alt && "Alt", binding.shift && "Shift", label[binding.key] ?? binding.key.toUpperCase()].filter(Boolean).join("+");
}

// These keys are dispatched by the grid hooks / tile controls owned by Lane K.
// Registering their data lets the provider guard them without replacing gestures.
export const gridBindings: HotkeyBinding[] = [
  { key: "Enter", description: "Open focused tile" },
  { key: " ", description: "Toggle focused tile" },
  ...["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].map(key => ({ key, description: "Move focus between tiles" })),
];

export const selectAllBindings: HotkeyBinding[] = [
  { key: "a", ctrl: true, description: "Select all loaded / clear" },
  { key: "a", meta: true, description: "Select all loaded / clear" },
];

export const selectionBindings: HotkeyBinding[] = [
  { key: "Delete", description: "Delete selected files…", destructive: true },
  { key: "Delete", shift: true, description: "Remove selected records…", destructive: true },
  { key: "x", description: "Blacklist selected items…", destructive: true },
  { key: "a", description: "Assign selection to a person…" },
  { key: "m", description: "Move selection to a folder…" },
  { key: "l", description: "Add selection to an album…" },
  { key: "t", description: "Add selection to a dataset…" },
  { key: "r", description: "Rerun processors for selection…" },
];

/** Shared guard for an existing element-level handler that retains dispatch. */
export function isHotkeyAllowed(event: KeyboardEvent): boolean {
  return !event.defaultPrevented && !event.isComposing && event.keyCode !== 229 &&
    !isEditableTarget(event.target) && !isMenuOpen() && !getTopModal();
}
