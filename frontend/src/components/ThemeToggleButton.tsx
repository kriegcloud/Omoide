import { IconButton, Tooltip } from "@mui/material";
import DarkModeOutlinedIcon from "@mui/icons-material/DarkModeOutlined";
import LightModeOutlinedIcon from "@mui/icons-material/LightModeOutlined";
import { useThemeContext } from "../ThemeContext";

/** One header icon button, sized like its neighbours; shows the mode it switches to. */
export default function ThemeToggleButton() {
  const { mode, toggleTheme } = useThemeContext();
  const next = mode === "dark" ? "light" : "dark";
  return (
    <Tooltip title={`Switch to ${next} mode`} arrow>
      <IconButton
        onClick={() => toggleTheme()}
        aria-label={`Switch to ${next} mode`}
        size="large"
        sx={{ p: 1, color: "text.secondary", "&:hover": { color: "text.primary" } }}
      >
        {mode === "dark" ? <LightModeOutlinedIcon /> : <DarkModeOutlinedIcon />}
      </IconButton>
    </Tooltip>
  );
}
