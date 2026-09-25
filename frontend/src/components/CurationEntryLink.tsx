import { useEffect, useState } from "react";
import { Button } from "@mui/material";
import { Link } from "react-router-dom";
import { getCurationStatus } from "../services/curation";

export default function CurationEntryLink() {
  const [mode, setMode] = useState<"fixture" | "production" | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void getCurationStatus(controller.signal).then(status => {
      if (!controller.signal.aborted) setMode(status.enabled && (status.mode === "fixture" || status.mode === "production") ? status.mode : null);
    }).catch(() => {});
    return () => controller.abort();
  }, []);
  return mode ? <Button component={Link} to="/curation" variant="outlined">{mode === "fixture" ? "Still review (fixtures)" : "Still review"}</Button> : null;
}
