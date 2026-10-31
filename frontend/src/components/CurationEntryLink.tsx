import { useEffect, useState } from "react";
import { Button } from "@mui/material";
import { Link } from "react-router-dom";
import { getCurationStatus } from "../services/curation";

export default function CurationEntryLink() {
  const [enabled, setEnabled] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void getCurationStatus(controller.signal).then(status => {
      if (!controller.signal.aborted) setEnabled(status.enabled && status.fixture_only);
    }).catch(() => {});
    return () => controller.abort();
  }, []);
  return enabled ? <Button component={Link} to="/curation" variant="outlined">Still review (fixtures)</Button> : null;
}
