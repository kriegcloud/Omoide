import type { HTMLAttributes, ReactNode } from "react";
import { Box, Checkbox } from "@mui/material";
import type { SxProps, Theme } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import type { SelectionClickEvent } from "../hooks/useMarqueeSelection";

interface SelectableTileFrameProps
  extends Omit<HTMLAttributes<HTMLDivElement>, "id" | "children" | "onClick"> {
  id: number;
  selected: boolean;
  selecting: boolean;
  onSelectionClick: (id: number, event: SelectionClickEvent) => boolean;
  href?: string;
  linkState?: unknown;
  replace?: boolean;
  onOpen?: () => void;
  menu?: ReactNode;
  topLeft?: ReactNode;
  bottomLeft?: ReactNode;
  bottomRight?: ReactNode;
  aspectRatio?: number | string;
  sx?: SxProps<Theme>;
  children: ReactNode;
  footer?: ReactNode;
}

const CONTROL_SELECTOR = "[data-tile-control], button, input, select, textarea, [contenteditable]";

const selectionToggleEvent: SelectionClickEvent = {
  ctrlKey: true,
  metaKey: false,
  shiftKey: false,
  altKey: false,
  preventDefault() {},
  stopPropagation() {},
};

const scrim = {
  position: "absolute",
  top: 6,
  zIndex: 20,
  bgcolor: "rgba(0,0,0,.45)",
  borderRadius: "8px",
} as const;

export default function SelectableTileFrame({
  id,
  selected,
  selecting,
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
  sx = [],
  children,
  footer,
  ...rest
}: SelectableTileFrameProps) {
  const content = <Box sx={{ position: "relative", aspectRatio }}>{children}</Box>;

  return (
    <Box
      {...rest}
      data-selectable-id={id}
      role={selecting ? "checkbox" : undefined}
      aria-checked={selecting ? selected : undefined}
      tabIndex={selecting || (!href && onOpen) ? 0 : undefined}
      onClickCapture={(event) => {
        const target = event.target;
        // Menus can render portals; their actions and the checkbox own clicks.
        if (
          !(target instanceof Element) ||
          !event.currentTarget.contains(target) ||
          target.closest(CONTROL_SELECTOR)
        ) return;
        if (onSelectionClick(id, event)) {
          event.preventDefault();
          event.stopPropagation();
        }
      }}
      onClick={(event) => {
        const target = event.target;
        if (
          !event.defaultPrevented && target instanceof Element &&
          event.currentTarget.contains(target) && !target.closest(CONTROL_SELECTOR) && onOpen
        ) onOpen();
      }}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (selecting && event.key === " ") {
          event.preventDefault();
          event.stopPropagation();
          onSelectionClick(id, selectionToggleEvent);
        } else if (!selecting && onOpen && (event.key === "Enter" || event.key === " ")) {
          event.preventDefault();
          onOpen();
        }
      }}
      sx={[
        {
          position: "relative",
          overflow: "hidden",
          borderRadius: 3,
          bgcolor: "background.paper",
          outline: selected ? "3px solid" : "none",
          outlineColor: "primary.main",
          outlineOffset: "-3px",
          transition: "all 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
          "&:hover": {
            transform: selecting ? "none" : "translateY(-4px)",
            boxShadow: selecting ? "none" : "0 12px 24px -8px rgba(0, 0, 0, 0.15)",
            zIndex: 10,
          },
          "&:hover .tile-checkbox, &:focus-within .tile-checkbox": {
            opacity: 1,
            pointerEvents: "auto",
          },
          "&:hover .tile-top-left, &:focus-within .tile-top-left": { left: 44 },
          "@media (hover: none)": {
            "& .tile-checkbox": { opacity: 1, pointerEvents: "auto" },
            "& .tile-top-left": { left: 44 },
          },
        },
        ...(Array.isArray(sx) ? sx : [sx]),
      ]}
    >
      <Box sx={{ position: "relative" }}>
        {href ? (
          <RouterLink
            to={href}
            state={linkState}
            replace={replace}
            draggable={false}
            tabIndex={selecting ? -1 : undefined}
            style={{ display: "block", textDecoration: "none", color: "inherit" }}
          >
            {content}
          </RouterLink>
        ) : content}
        <Checkbox
          className="tile-checkbox"
          data-tile-control
          data-no-marquee
          checked={selected}
          tabIndex={selecting ? -1 : 0}
          inputProps={{ "aria-label": `Select item ${id}`, readOnly: true }}
          onClick={(event) => {
            // This control is a sibling of the link, so native checkbox behavior
            // can finish without navigating or reaching the tile click handler.
            event.stopPropagation();
            onSelectionClick(id, selectionToggleEvent);
          }}
          sx={{
            ...scrim,
            left: 6,
            width: 32,
            height: 32,
            p: 0.5,
            color: "common.white",
            opacity: selecting || selected ? 1 : 0,
            pointerEvents: selecting || selected ? "auto" : "none",
            "&.Mui-checked": { color: "primary.main" },
            "&:hover": { bgcolor: "rgba(0,0,0,.65)" },
          }}
        />
        {!selecting && topLeft && (
          <Box className="tile-top-left" sx={{ position: "absolute", top: 6, left: selected ? 44 : 6, zIndex: 19, pointerEvents: "none" }}>
            {topLeft}
          </Box>
        )}
        {!selecting && menu && (
          <Box data-tile-control data-no-marquee sx={{ ...scrim, right: 6 }}>
            {menu}
          </Box>
        )}
        {bottomLeft && <Box sx={{ position: "absolute", bottom: 6, left: 6 }}>{bottomLeft}</Box>}
        {bottomRight && <Box sx={{ position: "absolute", bottom: 6, right: 6 }}>{bottomRight}</Box>}
      </Box>
      {footer}
    </Box>
  );
}
