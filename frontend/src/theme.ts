import type { ThemeMode } from "./ThemeContext";
import { alpha, createTheme } from "@mui/material";

// Forest green / zinc from beep Brand.tokens.ts and TrustGraph Workbench.
// Light surfaces are warmed to parchment; functional foregrounds stay zinc.
const mediaPalette = {
  dark: {
    primary: { main: "#5c9a5c", light: "#82b582", dark: "#3d7d3d", contrastText: "#09090b" },
    secondary: { main: "#a1a1aa", light: "#d4d4d8", dark: "#71717a", contrastText: "#09090b" },
    accent: { main: "#5c9a5c", light: "#82b582", dark: "#3d7d3d" },
    background: { default: "#09090b", paper: "#111113" },
    text: { primary: "#fafafa", secondary: "#a1a1aa", disabled: "#71717a" },
    divider: "#27272a",
    success: { main: "#22c55e", contrastText: "#09090b" },
    warning: { main: "#eab308", contrastText: "#09090b" },
    error: { main: "#ef4444", contrastText: "#09090b" },
    info: { main: "#a1a1aa", contrastText: "#09090b" },
  },
  light: {
    primary: { main: "#2d632d", light: "#3d7d3d", dark: "#214e21", contrastText: "#fafafa" },
    secondary: { main: "#52525b", light: "#71717a", dark: "#27272a", contrastText: "#fafafa" },
    accent: { main: "#2d632d", light: "#3d7d3d", dark: "#214e21" },
    background: { default: "#faf8f3", paper: "#f4f1ea" },
    text: { primary: "#18181b", secondary: "#52525b", disabled: "#71717a" },
    divider: "#d4d4d8",
    success: { main: "#16a34a", contrastText: "#09090b" },
    warning: { main: "#854d0e", contrastText: "#fafafa" },
    error: { main: "#b91c1c", contrastText: "#fafafa" },
    info: { main: "#52525b", contrastText: "#fafafa" },
  },
} as const;

export const getTheme = (mode: ThemeMode) => {
  const colors = mediaPalette[mode];
  const p = {
    ...colors,
    overlays: {
      elevated: colors.background.paper,
      hover: alpha(colors.text.primary, mode === "dark" ? 0.06 : 0.04),
      active: alpha(colors.text.primary, 0.12),
      focus: alpha(colors.primary.main, 0.3),
      selection: alpha(colors.primary.main, 0.16),
    },
  };
  return createTheme({
    palette: {
      mode, ...colors,
      action: { hover: p.overlays.hover, selected: p.overlays.selection, focus: p.overlays.focus },
    },
    shape: { borderRadius: 4 },
    typography: {
      fontFamily: '"Inter Variable", "Inter", ui-sans-serif, system-ui, sans-serif',
      h1: { fontWeight: 700 }, h2: { fontWeight: 700 },
      h3: { fontWeight: 600 }, h4: { fontWeight: 600 },
      h5: { fontWeight: 600 }, h6: { fontWeight: 600 },
      button: { fontWeight: 600 },
    },
    components: {
      MuiCssBaseline: {
        styleOverrides: (themeParam) => ({
          // Set the standard color on each scroll owner: Chromium gives it
          // precedence over WebKit pseudo-elements (including on nested boxes).
          '*, html, body': {
            scrollbarWidth: 'thin',
            scrollbarColor: 'transparent transparent',
            scrollbarGutter: 'stable',
          },
          '*:hover, *:focus-within, html:hover, html:focus-within, body:hover, body:focus-within': {
            scrollbarColor: `${alpha(themeParam.palette.text.primary, 0.25)} transparent`,
          },
          '*::-webkit-scrollbar': { width: '8px', height: '8px' },
          '*::-webkit-scrollbar-track, *::-webkit-scrollbar-corner': { background: 'transparent' },
          '*::-webkit-scrollbar-thumb': { backgroundColor: 'transparent', borderRadius: '8px' },
          '*:hover::-webkit-scrollbar-thumb, *:focus-within::-webkit-scrollbar-thumb': {
            backgroundColor: alpha(themeParam.palette.text.primary, 0.25),
          },
          '*::-webkit-scrollbar-thumb:hover': {
            backgroundColor: alpha(themeParam.palette.text.primary, 0.4),
          },
          'html, body, #root': {
            backgroundColor: themeParam.palette.background.default,
            color: themeParam.palette.text.primary,
            minHeight: '100%',
            scrollBehavior: 'smooth',
          },
          body: {
            "--color-raised": mode === "dark" ? "#18181b" : "#eeeae1",
            // CSS custom properties for app-wide use
            "--color-bg": themeParam.palette.background.default,
            "--color-paper": themeParam.palette.background.paper,
            "--color-text": themeParam.palette.text.primary,
            "--color-text-secondary": themeParam.palette.text.secondary,
            "--color-divider": themeParam.palette.divider,
            "--color-primary": themeParam.palette.primary.main,
            "--color-secondary": themeParam.palette.secondary.main,
            "--color-accent": themeParam.palette.accent.main,
            "--overlay-hover": p.overlays.hover,
            "--overlay-active": p.overlays.active,
            "--overlay-focus": p.overlays.focus,
            "--overlay-selection": p.overlays.selection,
          },
          'code, pre, kbd, samp': { fontFamily: 'var(--font-mono)' },
          '.MuiPaper-root': {
            backgroundColor: themeParam.palette.background.paper,
            color: themeParam.palette.text.primary,
          },
          '.MuiDialog-paper': {
            backgroundColor: themeParam.palette.background.paper,
            color: themeParam.palette.text.primary,
          },
        }),
      },
      MuiContainer: {
        styleOverrides: { root: ({ theme }) => ({
          paddingLeft: theme.spacing(1), paddingRight: theme.spacing(1),
          [theme.breakpoints.up("sm")]: { paddingLeft: theme.spacing(1.5), paddingRight: theme.spacing(1.5) },
        }) },
      },
      MuiPaper: {
        styleOverrides: { root: { backgroundImage: "none", backgroundColor: p.background.paper, borderRadius: 4 },
          elevation1: { boxShadow: "0 1px 3px rgba(0,0,0,.12)" },
          elevation2: { backgroundColor: mode === "dark" ? "#18181b" : "#eeeae1", boxShadow: "0 2px 8px rgba(0,0,0,.16)" },
        },
      },
      MuiDialog: { styleOverrides: { paper: { borderRadius: 6 } } },
      MuiAppBar: { styleOverrides: { colorPrimary: {
        backgroundColor: p.background.paper, color: p.text.primary,
        borderBottom: `1px solid ${p.divider}`, boxShadow: "none",
      } } },
      MuiButton: { styleOverrides: { root: {
        textTransform: "none", borderRadius: 4, minHeight: 32, padding: "4px 12px",
      }, containedPrimary: { boxShadow: "none", "&:hover": { boxShadow: "none" } } } },
      MuiIconButton: { styleOverrides: { root: {
        minWidth: 32, minHeight: 32,
        "&:hover": { background: p.overlays.hover },
        "&.Mui-focusVisible": { boxShadow: `0 0 0 3px ${p.overlays.focus}` },
      } } },
      MuiToggleButton: { styleOverrides: { root: { borderRadius: 4, minHeight: 32 } } },
      MuiChip: { styleOverrides: {
        root: { borderRadius: 4, fontWeight: 500 },
        colorPrimary: { background: p.primary.main, color: p.primary.contrastText },
        colorInfo: ({ ownerState }) => {
          const outlined = ownerState.variant === "outlined";
          const background = outlined ? p.background.paper : p.info.main;
          const color = outlined ? p.info.main : p.info.contrastText;
          return { background, color, ...(outlined ? { borderColor: color } : {}),
            "&.MuiChip-clickable:hover, &.Mui-focusVisible": { background, color, boxShadow: `inset 0 0 0 1px ${color}` } };
        },
      } },
      MuiTabs: { styleOverrides: { indicator: { backgroundColor: p.primary.main, height: 2 } } },
      MuiTab: { styleOverrides: { root: { textTransform: "none", fontWeight: 600, fontSize: "0.9rem", minHeight: 40 } } },
      MuiTooltip: { styleOverrides: { tooltip: { backgroundColor: p.text.primary, color: p.background.paper, borderRadius: 4, fontSize: "0.8rem" } } },
      MuiListItemButton: { styleOverrides: { root: {
        borderRadius: 4, minHeight: 32, margin: "2px 4px",
        "&:hover": { background: p.overlays.hover },
        "&.Mui-selected": { background: p.overlays.selection, color: p.primary.main,
          "& .MuiListItemIcon-root": { color: p.primary.main } },
      } } },
      MuiDivider: { styleOverrides: { root: { borderColor: p.divider } } },
    },
  });
};

declare module "@mui/material/styles" {
  interface Palette { accent: Palette["primary"]; }
  interface PaletteOptions { accent?: PaletteOptions["primary"]; }
}
