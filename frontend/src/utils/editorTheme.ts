import { alpha, type Theme } from "@mui/material";

/** Scaleflex tokens consumed by Filerobot 4.9.1 and its menus/controls.
 * Keep the legacy icon spelling and Filerobot's access-primary typo too.
 * Opaque control states deliberately preserve AA contrast in both app modes.
 */
export function buildEditorTheme(theme: Theme) {
  const p = theme.palette;
  const palette: Record<string, string> = {};
  const assign = (keys: string[], color: string) => {
    for (const key of keys) palette[key] = color;
  };
  assign(["txt-primary"], p.text.primary);
  assign([
    "txt-secondary", "txt-secondary-invert", "txt-placeholder", "icon-primary",
    "icons-primary-opacity-0-6", "icons-secondary", "icons-placeholder", "icons-invert",
    "icons-muted", "icons-primary-hover", "icons-secondary-hover", "btn-disabled-text",
    "btn-secondary-text", "borders-primary", "borders-primary-hover", "borders-secondary",
    "borders-strong", "borders-invert", "border-hover-bottom", "border-active-bottom",
    "border-primary-stateless", "borders-disabled", "borders-button", "borders-item",
    "borders-base-light", "borders-base-medium", "borders-green", "borders-green-medium",
    "borders-red", "tag", "blue", "indigo", "violet", "pink", "red-0-1-overlay",
    "green-0-2-Overlay", "camera", "google-drive", "dropbox", "one-drive", "device", "instagram",
    "free-images", "free-icons", "box", "screen-cast", "unsplash", "icons-primary"
  ], p.text.secondary);
  assign(["txt-warning", "warning", "warning-hover", "warning-active", "orange", "orange-0-1-overlay"], p.warning.main);
  assign(["txt-error", "states-error-disabled-text", "error", "error-hover", "error-active", "red"], p.error.main);
  assign(["txt-info", "info"], p.info.main);
  assign([
    "accent-primary", "accent-primary-hover", "accent-primary-active", "accent-stateless",
    "link-primary", "link-stateless", "link-hover", "link-active", "link-muted", "link-pressed",
    "active-secondary", "active-secondary-hover", "modified", "link", "access-primary"
  ], p.primary.main);
  assign([
    "accent-primary-disabled", "accent-secondary-disabled", "bg-grey", "bg-stateless", "bg-active",
    "bg-base-light", "bg-base-medium", "bg-primary-light", "bg-primary-hover", "bg-primary-active",
    "bg-primary-stateless", "bg-secondary", "bg-green", "bg-green-medium", "bg-blue", "bg-red",
    "bg-red-light", "background-red-medium", "bg-orange", "bg-tooltip"
  ], p.background.paper);
  assign(["accent-stateless_0_4_opacity", "accent_4_0_opacity"], alpha(p.primary.main, 0.4));
  assign(["accent_0_5_5_opacity"], alpha(p.primary.main, 0.55));
  assign(["accent_0_5_opacity"], alpha(p.primary.main, 0.05));
  assign(["accent_0_7_opacity"], alpha(p.primary.main, 0.7));
  assign(["accent_1_2_opacity"], alpha(p.primary.main, 0.12));
  assign(["accent_1_8_opacity"], alpha(p.primary.main, 0.18));
  assign(["accent_2_8_opacity"], alpha(p.primary.main, 0.28));
  assign(["bg-primary", "bg-hover", "canvas"], p.background.default);
  assign(["bg-primary-0-5-opacity"], alpha(p.background.paper, 0.5));
  assign(["btn-primary-text", "btn-primary-text-0-6", "btn-primary-text-0-4"], p.primary.contrastText);
  assign(["error-0-28-opacity"], alpha(p.error.main, 0.28));
  assign(["error-0-12-opacity"], alpha(p.error.main, 0.12));
  assign(["success", "success-hover", "success-Active", "salad", "green"], p.success.main);
  assign([
    "gradient-right", "gradient-right-active", "gradient-right-hover"
  ], `linear-gradient(${p.background.default}, ${p.background.paper})`);
  assign(["extra-0-3-overlay"], alpha(p.background.default, .3));
  assign(["extra-0-5-overlay"], alpha(p.background.default, .5));
  assign(["extra-0-7-overlay"], alpha(p.background.default, .7));
  assign(["extra-0-9-overlay"], alpha(p.background.default, .9));
  assign(["accent-0-8-overlay"], alpha(p.primary.main, 0.8));
  assign([
    "light-shadow", "medium-shadow", "large-shadow", "x-large-shadow"
  ], `0 2px 8px ${alpha(p.text.primary, 0.2)}`);
  return { palette, typography: { fontFamily: theme.typography.fontFamily, baseLineHeight: theme.typography.htmlFontSize / theme.typography.fontSize } };
}

/** The editor's styled-components controls also need a scoped foreground.
 * These stable FIE classes cover labels and native fields without relying on
 * generated sc-* class names or changing the canvas/preview appearance.
 */
export function buildEditorControlStyles(theme: Theme) {
  const p = theme.palette;
  return {
    colorScheme: p.mode,
    "& .FIE_tools-bar-wrapper": { color: p.text.primary, backgroundColor: p.background.paper },
    "& .FIE_tools-bar-wrapper label, & .FIE_tools-bar-wrapper [class*='Label'], & .FIE_tool-options-wrapper": {
      color: `${p.text.primary} !important`,
    },
    "& .FIE_tools-bar-wrapper input": {
      color: `${p.text.primary} !important`, WebkitTextFillColor: p.text.primary,
      backgroundColor: `${p.background.paper} !important`, caretColor: p.text.primary,
    },
    "& .FIE_tools-bar-wrapper input::placeholder": { color: p.text.secondary, opacity: 1 },
    "& .FIE_tools-bar-wrapper button, & .FIE_tools-bar-wrapper [role='button']": { color: p.text.primary },
    "& .FIE_tools-bar-wrapper :focus-visible": { outline: `2px solid ${p.primary.main}`, outlineOffset: 2 },
  };
}
