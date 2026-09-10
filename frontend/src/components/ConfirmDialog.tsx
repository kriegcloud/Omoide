import {
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
} from "@mui/material";
import React, { useId, useRef } from "react";
import { useDialogHotkeyScope, useHotkey } from "../hotkeys/useHotkey";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  confirmColor?: "primary" | "error" | "warning";
  loading?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

/**
 * Shared confirmation dialog for destructive or bulk actions. Use this instead
 * of window.confirm so confirmation UX is consistent across all views.
 */
export default function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  confirmColor = "error",
  loading = false,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  const titleId = useId();
  const dialogRef = useDialogHotkeyScope(open);
  const confirming = useRef(false);
  if (!open) confirming.current = false;
  const confirm = async () => {
    if (loading || confirming.current) return;
    confirming.current = true;
    try { await onConfirm(); } finally { confirming.current = false; }
  };
  useHotkey({ key: "Enter" }, () => { void confirm(); }, {
    scope: "dialog", dialogRef, enabled: open, description: confirmLabel, destructive: true,
  });
  return (
    <Dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      open={open}
      onClose={loading ? undefined : onClose}
      maxWidth="xs"
      fullWidth
    >
      <DialogTitle id={titleId}>{title}</DialogTitle>
      <DialogContent>
        {typeof message === "string" ? (
          <DialogContentText>{message}</DialogContentText>
        ) : (
          message
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={loading}>
          {cancelLabel}
        </Button>
        <Button
          autoFocus
          onClick={() => void confirm()}
          color={confirmColor}
          variant="contained"
          disabled={loading}
          startIcon={loading ? <CircularProgress size={16} /> : undefined}
        >
          {confirmLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
