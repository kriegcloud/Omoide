import { useContext, useLayoutEffect, useRef } from "react";
import { HotkeyContext, type HotkeyOptions } from "./HotkeyContext";
import type { HotkeyBinding } from "./keymap";

export function useHotkeys(bindings: readonly HotkeyBinding[], handler: (event: KeyboardEvent) => void | boolean, options: HotkeyOptions = {}) {
  const { register } = useContext(HotkeyContext);
  const latest = useRef({ bindings, handler, options });
  latest.current = { bindings, handler, options };
  const signature = JSON.stringify(bindings);
  useLayoutEffect(() => register(() => latest.current), [register, signature, options.scope, options.enabled, options.description]);
}

export function useHotkey(binding: HotkeyBinding, handler: (event: KeyboardEvent) => void | boolean, options: HotkeyOptions = {}) {
  useHotkeys([binding], handler, options);
}

/** Attach to the owning MUI Dialog root and pass as options.dialogRef. */
export function useDialogHotkeyScope(open: boolean) {
  const ref = useRef<HTMLDivElement>(null);
  useHotkey({ key: "Escape" }, () => {}, {
    scope: "dialog", dialogRef: ref, enabled: open, local: true, description: "Close dialog",
  });
  return ref;
}

export const useHotkeyHelp = () => useContext(HotkeyContext).openHelp;

export const useHotkeyRegistry = () => useContext(HotkeyContext).register;
