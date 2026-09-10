import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { HotkeyContext, type HelpBinding, type HotkeyRegistration } from "./HotkeyContext";
import { getTopModal, gridBindings, isEditableTarget, isMenuOpen, matchesBinding, scopePriority } from "./keymap";
import { useHotkey, useHotkeys } from "./useHotkey";
import { useSelection } from "../context/SelectionContext";
import { useUndo } from "../context/UndoContext";
import { GridKeyboardNavigation } from "./GridKeyboardNavigation";
import HotkeyHelpDialog from "./HotkeyHelpDialog";

function GlobalHotkeys() {
  const { selectedIds, setSelected, clear, isSelecting, toggleSelecting } = useSelection();
  const { current } = useUndo();
  useHotkey({ key: "s" }, toggleSelecting, { scope: "global", description: "Toggle select mode" });
  useHotkey({ key: "Escape" }, () => {
    if (selectedIds.size) setSelected([]);
    else clear();
  }, { scope: "global", enabled: selectedIds.size > 0 || isSelecting, description: "Clear selection, then exit select mode" });
  useHotkey({ key: "z", ctrl: true }, () => {
    // Reuse the snackbar's single-flight handler and failure feedback. Calling the
    // undo slot directly would race its button and could replay a partial inverse.
    const button = Array.from(document.querySelectorAll<HTMLButtonElement>('.MuiSnackbar-root button')).find(button => button.textContent?.trim() === "Undo");
    button?.click();
  }, { scope: "global", enabled: !!current, destructive: true, description: "Undo the pending action" });
  useHotkeys(gridBindings, () => {}, { scope: "global", local: true,
    when: () => !!document.querySelector('[data-selection-grid], [data-roving-tile]') });
  return null;
}

export function HotkeyProvider({ children }: { children: ReactNode }) {
  const registrations = useRef(new Map<symbol, () => HotkeyRegistration>());
  const [help, setHelp] = useState<HelpBinding[] | null>(null);
  const register = useCallback((read: () => HotkeyRegistration) => {
    const token = Symbol();
    registrations.current.set(token, read);
    return () => { registrations.current.delete(token); };
  }, []);

  const activeEntries = useCallback(() => Array.from(registrations.current.values(), read => read())
    .filter(entry => entry.options.enabled !== false)
    .flatMap(entry => entry.bindings.map(binding => ({ ...entry, binding, scope: binding.scope ?? entry.options.scope ?? "global" })))
    .reverse().sort((a, b) => scopePriority[b.scope] - scopePriority[a.scope] || Number(a.options.local ?? false) - Number(b.options.local ?? false)), []);

  const openHelp = useCallback(() => {
    const modal = getTopModal();
    const seen = new Set<string>();
    const event = new KeyboardEvent("keydown");
    Object.defineProperty(event, "target", { value: document.activeElement });
    const entries = activeEntries().filter(entry => {
      if (modal ? entry.scope !== "dialog" || !entry.options.dialogRef?.current?.contains(modal) : entry.scope === "dialog") return false;
      if (entry.options.when && !entry.options.when(event)) return false;
      const signature = JSON.stringify([entry.binding.key, !!entry.binding.ctrl, !!entry.binding.shift, !!entry.binding.meta, !!entry.binding.alt]);
      if (seen.has(signature)) return false;
      seen.add(signature);
      return true;
    });
    setHelp(entries.map(entry => ({ ...entry.binding, scope: entry.scope, description: entry.binding.description ?? entry.options.description })));
  }, [activeEntries]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || event.keyCode === 229) return;
      const target = event.composedPath()[0] ?? event.target;
      const modal = getTopModal();
      const menu = isMenuOpen();
      const entries = activeEntries().filter(entry => matchesBinding(entry.binding, event));
      for (const entry of entries) {
        const { options, binding, scope } = entry;
        const eligibleScope = modal
          ? scope === "dialog" && !!options.dialogRef?.current?.contains(modal)
          : scope !== "dialog";
        if (!eligibleScope || (!options.allowInInput && isEditableTarget(target)) || (!options.allowInMenu && menu)) {
          continue;
        }
        if (options.when && !options.when(event)) continue;
        if (event.repeat && (options.destructive || binding.destructive)) {
          event.preventDefault();
          event.stopImmediatePropagation();
          return;
        }
        if (options.local) return; // The existing grid/tile handler owns dispatch.
        if (entry.handler(event) === false) continue;
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }

    };
    window.addEventListener("keydown", handleKeyDown, true);
    return () => window.removeEventListener("keydown", handleKeyDown, true);
  }, [activeEntries]);

  const context = useMemo(() => ({ register, openHelp }), [register, openHelp]);
  return <HotkeyContext.Provider value={context}>
    <GlobalHotkeys />
    <GridKeyboardNavigation />
    <HelpShortcut openHelp={openHelp} />
    {children}
    <HotkeyHelpDialog open={help !== null} bindings={help ?? []} onClose={() => setHelp(null)} />
  </HotkeyContext.Provider>;
}

function HelpShortcut({ openHelp }: { openHelp: () => void }) {
  useHotkey({ key: "?" }, openHelp, { scope: "global", description: "Show keyboard shortcuts" });
  return null;
}
