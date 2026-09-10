import React, { useMemo } from "react";
import { BrowserRouter as Router } from "react-router-dom";
import { ThemeProvider as MuiThemeProvider, CssBaseline } from "@mui/material";
import { useThemeContext } from "./ThemeContext";
import { getTheme } from "./theme";
import { TaskEventsProvider } from "./TaskEventsContext";
import { UndoProvider } from "./context/UndoContext";
import { SelectionProvider } from "./context/SelectionContext";
import { ScrollToTop } from "./components/ScrollToTop";
import { HotkeyProvider } from "./hotkeys/HotkeyProvider";
import { AppRoutes } from "./routes";

export default function App() {
  const { mode } = useThemeContext();
  const theme = useMemo(() => getTheme(mode), [mode]);

  return (
    <TaskEventsProvider>
      <MuiThemeProvider theme={theme}>
        <CssBaseline />
        <UndoProvider>
          <SelectionProvider>
            <Router>
              <HotkeyProvider>
                <ScrollToTop />
                <AppRoutes />
              </HotkeyProvider>
            </Router>
          </SelectionProvider>
        </UndoProvider>
      </MuiThemeProvider>
    </TaskEventsProvider>
  );
}
