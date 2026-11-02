import { useRef, type HTMLAttributes, type ReactNode } from "react";
import { Box, Checkbox, IconButton, Tooltip, useTheme } from "@mui/material";
import OpenInFullIcon from "@mui/icons-material/OpenInFull";
import type { SxProps, Theme } from "@mui/material";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import type { SelectionClickEvent } from "../hooks/useMarqueeSelection";
import { TileContextMenuContext, createTileContextMenuRegistry, tileMenuPoint, type TileContextMenuRegistry } from "./tileContextMenu";

interface SelectableTileFrameProps
  extends Omit<HTMLAttributes<HTMLDivElement>, "id" | "children" | "onClick"> {
  id: number;
  selected: boolean;
  selecting: boolean;
  indeterminate?: boolean;
  selectionEnabled?: boolean;
  keyboardReview?: boolean;
  // Omit for navigation-only card consumers: no selection checkbox is shown.
  onSelectionClick?: (id: number, event: SelectionClickEvent) => boolean;
  href?: string;
  linkState?: unknown;
  replace?: boolean;
  onOpen?: () => void;
  menu?: ReactNode;
  topLeft?: ReactNode;
  bottomLeft?: ReactNode;
  bottomRight?: ReactNode;
  aspectRatio?: number | string;
  /** Corner radius as a theme multiplier (theme.shape.borderRadius × radius). */
  radius?: number;
  sx?: SxProps<Theme>;
  children: ReactNode;
  footer?: ReactNode;
  // Let caption-only footers navigate to href as well as the thumbnail.
  linkFooter?: boolean;
}

const CONTROL_SELECTOR = "[data-tile-control], button, input, select, textarea, [contenteditable]";
const CONTROL_SIZE = 32;

/** A checkbox always toggles, while retaining the user's range modifiers. */
export function toSelectionGesture(event: Pick<SelectionClickEvent, "metaKey" | "shiftKey" | "altKey">): SelectionClickEvent {
  return {
    ctrlKey: true,
    metaKey: event.metaKey,
    shiftKey: event.shiftKey,
    altKey: event.altKey,
    preventDefault() {},
    stopPropagation() {},
  };
}

/**
 * Distance from the tile edge at which a square control clears a rounded
 * corner: the corner arc of radius R leaves the point (d, d) outside unless
 * d ≥ R·(1 − 1/√2) ≈ 0.3R. Anything smaller is clipped by overflow:hidden,
 * which is how the old checkbox/menu buttons lost their outer corners.
 */
function chromeInset(radiusPx: number): number {
  return Math.max(6, Math.ceil(radiusPx * 0.3));
}

export default function SelectableTileFrame({
  id,
  selected,
  selecting,
  indeterminate = false,
  selectionEnabled = true,
  keyboardReview = false,
  onSelectionClick,
  href,
  linkState,
  replace,
  onOpen,
  menu,
  topLeft,
  bottomLeft,
  bottomRight,
  aspectRatio,
  radius = 1,
  sx = [],
  children,
  footer,
  linkFooter = false,
  ...rest
}: SelectableTileFrameProps) {
  const theme = useTheme();
  const navigate = useNavigate();
  const firstClickUndo = useRef<(() => void) | null>(null);
  const open = () => {
    if (onOpen) onOpen();
    else if (href) navigate(href, { state: linkState, replace });
  };
  const radiusPx =
    typeof theme.shape.borderRadius === "number"
      ? theme.shape.borderRadius * radius
      : parseFloat(String(theme.shape.borderRadius)) * radius;
  const inset = chromeInset(radiusPx);
  const badgeLeft = inset + CONTROL_SIZE + 6;
  const scrim = {
    position: "absolute",
    top: inset,
    zIndex: 20,
    bgcolor: "rgba(0,0,0,.45)",
    borderRadius: "4px",
  } as const;
  const showCheckbox = selectionEnabled && !!onSelectionClick;
  const isSelecting = selectionEnabled && selecting;
  const isCheckbox = selectionEnabled && (selecting || keyboardReview);
  const content = <Box sx={{ position: "relative", aspectRatio }}>{children}</Box>;
  const contextMenuRef = useRef<TileContextMenuRegistry | null>(null);
  contextMenuRef.current ??= createTileContextMenuRegistry();
  const contextMenu = contextMenuRef.current;

  return (
    <Box
      {...rest}
      data-selectable-id={id}
      data-roving-tile={keyboardReview ? "" : undefined}
      role={isCheckbox ? "checkbox" : undefined}
      aria-label={isCheckbox ? `Select item ${id}` : undefined}
      aria-checked={isCheckbox ? (indeterminate ? "mixed" : selected) : undefined}
      tabIndex={keyboardReview ? -1 : isSelecting || (!href && onOpen) ? 0 : undefined}
      onClickCapture={(event) => {
        const target = event.target;
        // Menus can render portals; their actions and the checkbox own clicks.
        if (
          !(target instanceof Element) ||
          !event.currentTarget.contains(target) ||
          target.closest(CONTROL_SELECTOR)
        ) return;
        // The second click belongs to the pending double-click, even if the
        // first click deselected the last item and selecting is now false.
        if (event.detail === 2 && firstClickUndo.current) {
          event.preventDefault();
          event.stopPropagation();
          return;
        }
        firstClickUndo.current = null;
        let undo: (() => void) | undefined;
        const gesture: SelectionClickEvent = {
          ctrlKey: event.ctrlKey, metaKey: event.metaKey,
          shiftKey: event.shiftKey, altKey: event.altKey,
          preventDefault: () => event.preventDefault(),
          stopPropagation: () => event.stopPropagation(),
          registerUndo: (restore) => { undo = restore; },
        };
        if (selectionEnabled && onSelectionClick?.(id, gesture)) {
          if (event.detail === 1) {
            // Older consumers only toggle one id. Grid consumers register a
            // full snapshot so a Shift range and its previous anchor restore too.
            firstClickUndo.current = undo ?? (() => onSelectionClick?.(id,
              toSelectionGesture({ shiftKey: false, metaKey: false, altKey: false })));
          }
          event.preventDefault();
          event.stopPropagation();
        }
      }}
      onDoubleClick={(event) => {
        const target = event.target;
        if (!(target instanceof Element) || !event.currentTarget.contains(target) ||
          target.closest(CONTROL_SELECTOR) || !firstClickUndo.current) return;
        event.preventDefault();
        event.stopPropagation();
        firstClickUndo.current();
        firstClickUndo.current = null;
        open();
      }}
      onClick={(event) => {
        const target = event.target;
        if (
          !event.defaultPrevented && target instanceof Element &&
          event.currentTarget.contains(target) && !target.closest(CONTROL_SELECTOR) && onOpen
        ) onOpen();
      }}
      onContextMenu={(event) => {
        const handler = contextMenu.current();
        const point = tileMenuPoint(event, event.currentTarget.getBoundingClientRect(), !!handler);
        if (!handler || !point) return;
        event.preventDefault();
        event.stopPropagation();
        firstClickUndo.current = null;
        handler(point);
      }}
      onKeyDown={(event) => {
        const target = event.target;
        if (event.defaultPrevented || !(target instanceof Element) ||
          !event.currentTarget.contains(target) || target.closest(CONTROL_SELECTOR)) return;
        if (isCheckbox && event.key === " ") {
          event.preventDefault();
          event.stopPropagation();
          firstClickUndo.current = null;
          onSelectionClick?.(id, toSelectionGesture(event));
        } else if ((onOpen || href) && (event.key === "Enter" || (!isSelecting && event.key === " "))) {
          event.preventDefault();
          event.stopPropagation();
          firstClickUndo.current = null;
          open();
        }
      }}
      sx={[
        {
          position: "relative",
          overflow: "hidden",
          borderRadius: radius,
          bgcolor: "background.paper",
          outline: selected ? "3px solid" : "none",
          outlineColor: "primary.main",
          outlineOffset: "-3px",
          transition: "box-shadow 120ms ease",
          "&:hover": {
            transform: "none",
            boxShadow: isSelecting ? "none" : "0 12px 24px -8px rgba(0, 0, 0, 0.15)",
            zIndex: 10,
          },
          "&:hover .tile-checkbox, &:focus-within .tile-checkbox, &:hover .tile-open, &:focus-within .tile-open": {
            opacity: 1,
            pointerEvents: "auto",
          },
          "&:focus-visible": { outline: "3px solid", outlineColor: "primary.main", outlineOffset: "-3px" },
          "&:hover .tile-top-left, &:focus-within .tile-top-left": { left: badgeLeft },
          "@media (hover: none)": {
            "& .tile-checkbox, & .tile-open": { opacity: 1, pointerEvents: "auto" },
            "& .tile-top-left": { left: badgeLeft },
          },
        },
        ...(Array.isArray(sx) ? sx : [sx]),
      ]}
    >
      <TileContextMenuContext.Provider value={contextMenu}>
      <Box sx={{ position: "relative" }}>
        {href ? (
          <RouterLink
            to={href}
            state={linkState}
            replace={replace}
            draggable={false}
            tabIndex={isSelecting || keyboardReview ? -1 : undefined}
            style={{ display: "block", textDecoration: "none", color: "inherit" }}
          >
            {content}
          </RouterLink>
        ) : content}
        {showCheckbox && (
          <Checkbox
            className="tile-checkbox"
            data-tile-control
            data-no-marquee
            checked={selected}
            indeterminate={indeterminate}
            tabIndex={isSelecting || keyboardReview ? -1 : 0}
            inputProps={{ "aria-label": `Select item ${id}`, readOnly: true }}
            onClick={(event) => {
              // This control is a sibling of the link, so native checkbox behavior
              // can finish without navigating or reaching the tile click handler.
              event.stopPropagation();
              firstClickUndo.current = null;
              onSelectionClick?.(id, toSelectionGesture(event));
            }}
            sx={{
              ...scrim,
              // No dark square behind the box: the icon carries its own outline so it
              // reads on light and dark photos; hover gets a soft round backing.
              bgcolor: "transparent",
              borderRadius: "50%",
              left: inset,
              width: CONTROL_SIZE,
              height: CONTROL_SIZE,
              p: 0.5,
              color: "common.white",
              opacity: isSelecting || selected ? 1 : 0,
              pointerEvents: isSelecting || selected ? "auto" : "none",
              "& .MuiSvgIcon-root": { filter: "drop-shadow(0 0 1.5px rgba(0,0,0,0.85)) drop-shadow(0 1px 2px rgba(0,0,0,0.5))" },
              "&.Mui-checked, &.MuiCheckbox-indeterminate": { color: "primary.main" },
              "&.Mui-checked .MuiSvgIcon-root, &.MuiCheckbox-indeterminate .MuiSvgIcon-root": {
                // White behind the cut-out tick only (icon square is inset 3/24).
                bgcolor: "common.white", clipPath: "inset(12.5% round 2px)",
              },
              "&:hover": { bgcolor: "rgba(0,0,0,.35)" },
            }}
          />
        )}
        {isSelecting && (onOpen || href) && (
          <Tooltip title="Open">
            <IconButton
              className="tile-open"
              data-tile-control
              data-no-marquee
              aria-label="Open"
              onClick={(event) => {
                event.stopPropagation();
                firstClickUndo.current = null;
                open();
              }}
              sx={{
                ...scrim, right: inset, width: CONTROL_SIZE, height: CONTROL_SIZE,
                color: "common.white", opacity: 0, pointerEvents: "none",
                "&:hover": { bgcolor: "rgba(0,0,0,.65)" },
              }}
            >
              <OpenInFullIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}
        {!isSelecting && topLeft && (
          <Box
            className="tile-top-left"
            sx={{
              position: "absolute",
              top: inset,
              left: selected && showCheckbox ? badgeLeft : inset,
              zIndex: 19,
              pointerEvents: "none",
            }}
          >
            {topLeft}
          </Box>
        )}
        {!isSelecting && menu && (
          <Box data-tile-control data-no-marquee sx={{ position: "absolute", top: inset, right: inset, zIndex: 20 }}>
            {menu}
          </Box>
        )}
        {bottomLeft && <Box sx={{ position: "absolute", bottom: inset, left: inset }}>{bottomLeft}</Box>}
        {bottomRight && <Box sx={{ position: "absolute", bottom: inset, right: inset }}>{bottomRight}</Box>}
      </Box>
      {linkFooter && href ? (
        <RouterLink
          to={href}
          state={linkState}
          replace={replace}
          draggable={false}
          tabIndex={-1}
          style={{ display: "block", textDecoration: "none", color: "inherit" }}
        >
          {footer}
        </RouterLink>
      ) : footer}
      </TileContextMenuContext.Provider>
    </Box>
  );
}
