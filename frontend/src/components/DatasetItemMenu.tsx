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

export default function DatasetItemMenu({ context }: { context: MediaDatasetContext }) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const action = (callback: () => void) => {
    setAnchor(null);
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
            setAnchor(event.currentTarget);
          }}
          sx={{ color: "common.white", bgcolor: "rgba(0,0,0,0.48)", "&:hover": { bgcolor: "rgba(0,0,0,0.68)" } }}
        >
          <MoreVertIcon fontSize="small" />
        </IconButton>
      </Tooltip>
      <Menu anchorEl={anchor} open={Boolean(anchor)} onClose={() => setAnchor(null)}>
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
