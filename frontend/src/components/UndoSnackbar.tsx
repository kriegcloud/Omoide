import { useRef, useState } from "react";
import { Alert, Button, CircularProgress, Snackbar } from "@mui/material";
import { useUndo, type UndoableAction } from "../context/UndoContext";

function UndoMessage({ action, dismiss }: { action: UndoableAction; dismiss: () => void }) {
  const [status, setStatus] = useState<"ready" | "busy" | "done" | "error">("ready");
  const [error, setError] = useState("");
  const running = useRef(false);
  const undo = async () => {
    if (running.current) return;
    running.current = true;
    setStatus("busy");
    try {
      await action.undo();
      setStatus("done");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Undo failed");
      setStatus("error");
    }
    // Do not replay an inverse after a refresh failure or partial server success.
  };
  return (
    <Snackbar
      open
      autoHideDuration={status === "busy" || status === "error" ? null : status === "done" ? 3000 : action.ttlMs}
      onClose={(_, reason) => { if (reason !== "clickaway" && status !== "busy") dismiss(); }}
      anchorOrigin={{ vertical: "top", horizontal: "center" }}
    >
      <Alert
        severity={status === "error" ? "error" : status === "done" ? "success" : "info"}
        onClose={status === "busy" ? undefined : dismiss}
        action={status === "ready" || status === "busy" ? (
          <Button color="inherit" size="small" disabled={status === "busy"} onClick={() => void undo()}>
            {status === "busy" ? <CircularProgress size={16} color="inherit" aria-label="Undoing" /> : "Undo"}
          </Button>
        ) : undefined}
        sx={{ alignItems: "center" }}
      >
        {status === "done" ? "Undone" : status === "error" ? `Undo failed: ${error}` : action.label}
      </Alert>
    </Snackbar>
  );
}

export function UndoSnackbar() {
  const { current, dismiss } = useUndo();
  // Keying isolates in-flight results and timers when another action replaces it.
  return current ? <UndoMessage key={current.id} action={current} dismiss={dismiss} /> : null;
}
