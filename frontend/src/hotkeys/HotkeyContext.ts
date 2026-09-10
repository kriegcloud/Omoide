import { createContext, type RefObject } from "react";
import type { HotkeyBinding, HotkeyScope } from "./keymap";

export interface HotkeyOptions {
  scope?: HotkeyScope;
  enabled?: boolean;
  description?: string;
  when?: (event: KeyboardEvent) => boolean;
  destructive?: boolean;
  allowInMenu?: boolean;
  allowInInput?: boolean;
  dialogRef?: RefObject<HTMLElement | null>;
  /** Existing element/grid handlers retain dispatch; registry supplies help/guards. */
  local?: boolean;
}
export interface HotkeyRegistration {
  bindings: readonly HotkeyBinding[];
  handler: (event: KeyboardEvent) => void | boolean;
  options: HotkeyOptions;
}
export interface HelpBinding extends HotkeyBinding { scope: HotkeyScope }
export const HotkeyContext = createContext({
  register: (read: () => HotkeyRegistration): (() => void) => { void read; return () => {}; },
  openHelp: () => {},
});
