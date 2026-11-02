import { useState } from "react";
import {
  IconButton,
  ListItemIcon,
  Menu,
  MenuItem,
  Tooltip,
} from "@mui/material";
import MoreVertIcon from "@mui/icons-material/MoreVert";
import BlockIcon from "@mui/icons-material/Block";
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutline";
import EditNoteIcon from "@mui/icons-material/EditNote";
import CropIcon from "@mui/icons-material/Crop";
import RemoveCircleOutlineIcon from "@mui/icons-material/RemoveCircleOutline";
import type { MediaDatasetContext } from "./MediaCard";
import { useTileContextMenu, type TileMenuPoint } from "./tileContextMenu";

export default function DatasetItemMenu({ context }: { context: MediaDatasetContext }) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [point, setPoint] = useState<TileMenuPoint | null>(null);
  useTileContextMenu((next) => {
    setAnchor(null);
    setPoint(next);
  });
  const close = () => {
    setAnchor(null);
    setPoint(null);
  };
  const action = (callback: () => void) => {
    close();
    callback();
  };
  return (
    <>
      <Tooltip title="Dataset item actions">
        <IconButton
          aria-label="Dataset item actions"
          size="small"
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
            setPoint(null);
            setAnchor(event.currentTarget);
          }}
          sx={{ color: "common.white", bgcolor: "rgba(0,0,0,0.48)", "&:hover": { bgcolor: "rgba(0,0,0,0.68)" } }}
        >
          <MoreVertIcon fontSize="small" />
        </IconButton>
      </Tooltip>
      <Menu
        anchorReference={point ? "anchorPosition" : "anchorEl"}
        anchorPosition={point ?? undefined}
        anchorEl={anchor}
        open={Boolean(anchor) || Boolean(point)}
        onClose={close}
        onClick={(event) => event.stopPropagation()}
      >
        <MenuItem onClick={() => action(context.onToggleExcluded)}>
          <ListItemIcon>{context.excluded ? <CheckCircleOutlineIcon /> : <BlockIcon />}</ListItemIcon>
          {context.excluded ? "Include" : "Exclude"}
        </MenuItem>
        <MenuItem onClick={() => action(context.onEditCaption)}>
          <ListItemIcon><EditNoteIcon /></ListItemIcon>Edit caption…
        </MenuItem>
        <MenuItem onClick={() => action(context.onEditCrop)}>
          <ListItemIcon><CropIcon /></ListItemIcon>Edit crop…
        </MenuItem>
        <MenuItem onClick={() => action(context.onRemove)}>
          <ListItemIcon><RemoveCircleOutlineIcon color="error" /></ListItemIcon>Remove
        </MenuItem>
      </Menu>
    </>
  );
}
