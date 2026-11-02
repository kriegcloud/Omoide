import React from "react";
import { Box, CssBaseline } from "@mui/material";
import { Outlet } from "react-router-dom";
import { Header } from "./Header";
import { Sidebar } from "./Sidebar";
import { BinaryNavigationControls } from "./BinaryNavigationControls";
import ProfileSetupDialog from "./ProfileSetupDialog";
import { SelectionActionBar } from "./SelectionActionBar";

import { UndoSnackbar } from "./UndoSnackbar";
import { SelectionRouteSync } from "../context/SelectionContext";

export function Layout() {
  return (
    <Box sx={{ display: "flex", minHeight: "100vh",
      backgroundImage: (theme) => `radial-gradient(ellipse 60% 260px at 20% 0%, ${theme.palette.mode === "dark" ? "rgba(61,125,61,.15)" : "rgba(61,125,61,.10)"}, transparent 75%)` }}>
      <CssBaseline />
      <Sidebar />
      <Box sx={{ flexGrow: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        <Header />
        <BinaryNavigationControls variant="overlay" sx={{ position: "fixed", bottom: 24, left: 24, zIndex: 1200 }} />
        <Box component="main" sx={{ flexGrow: 1, p: 0, position: "relative" }}>
          <Outlet />
        </Box>
      </Box>
      <ProfileSetupDialog />
      <SelectionRouteSync />
      <SelectionActionBar />
      <UndoSnackbar />
    </Box>
  );
}
