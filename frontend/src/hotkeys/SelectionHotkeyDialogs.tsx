import { useState } from "react";
import { Alert, Snackbar } from "@mui/material";
import { useHotkeys } from "./useHotkey";
import { selectionBindings } from "./keymap";
import config from "../config";
import { bulkDeleteMedia, bulkMoveMedia, type BulkDeleteAction } from "../services/mediaActions";
import ConfirmDialog from "../components/ConfirmDialog";
import { resolveConfirmCopy } from "../components/BulkResolveToolbar";
import FolderPickerDialog from "../components/FolderPickerDialog";
import { AddToAlbumDialog } from "../components/AddToAlbumDialog";
import AddToDatasetDialog from "../components/AddToDatasetDialog";
import { RerunProcessorsDialog } from "../components/RerunProcessorsDialog";

/** Adds the common media actions to page-owned selections without changing their stores. */
export function SelectionHotkeyDialogs({ mediaIds, enabled = true, includeDelete = false, onProcessed }: {
  mediaIds: number[];
  enabled?: boolean;
  includeDelete?: boolean;
  onProcessed?: (ids: number[]) => void | Promise<void>;
}) {
  const [pending, setPending] = useState<{ kind: "m" | "l" | "t" | "r" | BulkDeleteAction; ids: number[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ message: string; error?: boolean } | null>(null);
  useHotkeys(selectionBindings.filter(binding => ["m", "l", "t", "r", ...(includeDelete ? ["Delete", "x"] : [])].includes(binding.key)), (event) => {
    const key = event.key.toLowerCase();
    const kind = key === "delete" ? event.shiftKey ? "DELETE_RECORDS" : "DELETE_FILES" : key === "x" ? "BLACKLIST_RECORDS" : key as "m" | "l" | "t" | "r";
    setPending({ kind, ids: [...new Set(mediaIds)] });
  }, { scope: "page", enabled: enabled && mediaIds.length > 0 && !busy && !config.PRESENTATION_MODE });
  const deleting = pending && ["DELETE_FILES", "DELETE_RECORDS", "BLACKLIST_RECORDS"].includes(pending.kind);
  const copy = deleting ? resolveConfirmCopy(pending.kind as BulkDeleteAction, pending.ids.length) : null;
  const fail = (error: unknown) => setFeedback({ error: true, message: error instanceof Error ? error.message : "Action failed" });
  return <>
    <ConfirmDialog open={!!deleting} title={copy?.title ?? ""} message={copy?.message ?? ""} confirmLabel={copy?.confirmLabel} loading={busy}
      onClose={() => setPending(null)} onConfirm={async () => {
        if (!pending || !deleting || busy || config.PRESENTATION_MODE) return;
        setBusy(true);
        try {
          const result = await bulkDeleteMedia(pending.ids, pending.kind as BulkDeleteAction);
          setPending(null);
          await onProcessed?.(result.processed_ids);
          setFeedback({ message: `Deleted ${result.removed}; ${result.skipped_ids.length} skipped; ${result.errors.length} failed`, error: result.errors.length > 0 });
        } catch (error) { fail(error); } finally { setBusy(false); }
      }} />
    <FolderPickerDialog open={pending?.kind === "m"} loading={busy} onClose={() => setPending(null)} onConfirm={async destination => {
      if (!pending || busy || config.PRESENTATION_MODE) return;
      setBusy(true);
      try {
        const result = await bulkMoveMedia(pending.ids, destination);
        setPending(null);
        setFeedback({ message: `Moved ${result.moved_ids.length}; ${result.skipped.length} skipped` });
      } catch (error) { fail(error); } finally { setBusy(false); }
    }} />
    <AddToAlbumDialog open={pending?.kind === "l"} mediaIds={pending?.ids ?? []} onClose={() => setPending(null)} onAdded={album => {
      setPending(null); setFeedback({ message: `Added to ${album.name}` });
    }} />
    <AddToDatasetDialog open={pending?.kind === "t"} mediaIds={pending?.ids ?? []} onClose={() => setPending(null)} onAdded={(dataset, added) => {
      setPending(null); setFeedback({ message: `Added ${added} item(s) to ${dataset.name}` });
    }} />
    <RerunProcessorsDialog open={pending?.kind === "r"} mediaIds={pending?.ids ?? []} onClose={() => setPending(null)} onStarted={() => {
      setPending(null); setFeedback({ message: "Processing started" });
    }} />
    <Snackbar open={feedback !== null} autoHideDuration={5000} onClose={() => setFeedback(null)}>
      <Alert severity={feedback?.error ? "error" : "success"} onClose={() => setFeedback(null)}>{feedback?.message}</Alert>
    </Snackbar>
  </>;
}
