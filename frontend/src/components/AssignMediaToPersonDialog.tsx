import { useUndo } from "../context/UndoContext";
import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from "@mui/material";
import { Person } from "../types";
import PersonPicker, { PERSON_PICKER_DIALOG_CONTENT_SX } from "./PersonPicker";
import {
  startPeopleMutation,
  attachMediaToPersonBulk,
  detachMediaFromPersonBulk,
  reassignMediaToPerson,
} from "../services/personActions";

interface AssignMediaToPersonDialogProps {
  open: boolean;
  mediaIds: number[];
  sourcePersonId?: number;
  onClose: () => void;
  onAssigned: (person: Person, skippedCount: number) => void;
}

export default function AssignMediaToPersonDialog({
  open,
  mediaIds,
  sourcePersonId,
  onClose,
  onAssigned,
}: AssignMediaToPersonDialogProps) {
  const { push, refreshVisible } = useUndo();
  const [assigning, setAssigning] = useState(false);
  const submitting = useRef(false);
  const generation = useRef(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    generation.current++;
    submitting.current = false;
    setAssigning(false);
    setError(null);
  }, [open]);

  const assign = (person: Person) => {
    if (submitting.current || !mediaIds.length) return;
    submitting.current = true;
    const revision = generation.current;
    const ids = [...mediaIds];
    const source = sourcePersonId;
    setAssigning(true);
    let actualSkipped = ids.length;
    let addedIds: number[] = [];
    const operation = startPeopleMutation("Assign media to person", [person.id], async () => {
      let skippedCount = 0;
      if (source !== undefined && ids.length === 1) {
        const result = await reassignMediaToPerson(source, ids[0], person.id);
        skippedCount = result.reassigned ? 0 : 1;
      } else {
        const result = await attachMediaToPersonBulk(person.id, ids);
        skippedCount = result.skipped_ids.length;
        addedIds = [...(result.added_ids ?? [])];
      }
      actualSkipped = skippedCount;
      return { skippedCount };
    }, { durable: false, resources: source === undefined ? [] : [source], entityResources: ids.map(id => `media:${id}` as const), refresh: async () => {
      if (revision === generation.current) onAssigned(person, actualSkipped);
    }, applied: result => result.skippedCount < ids.length ? [person.id] : [] });
    onClose();
    void operation.done.then(record => {
      if (record.receipt && addedIds.length) push({
        label: `Assigned ${addedIds.length} item(s) to ${person.name ?? "person"}`,
        undo: async () => {
          const inverse = startPeopleMutation("Undo media assignment", [person.id],
            () => detachMediaFromPersonBulk(person.id, addedIds), {
              durable: false, entityResources: addedIds.map(id => `media:${id}` as const),
              applied: result => result.skipped_ids.length ? [] : [person.id],
            });
          const restored = await inverse.done;
          await refreshVisible();
          if (restored.state !== "committed") throw new Error("Some media could not be detached");
        },
      });
      if (revision === generation.current) {
        submitting.current = false;
        setAssigning(false);
      }
    });
  };

  return (
    <Dialog open={open} onClose={assigning ? undefined : onClose} fullWidth maxWidth="xs">
      <DialogTitle>Assign to person</DialogTitle>
      <DialogContent dividers sx={PERSON_PICKER_DIALOG_CONTENT_SX}>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        {open && (
          <PersonPicker
            autoFocus
            excludeIds={sourcePersonId === undefined ? [] : [sourcePersonId]}
            disabled={assigning}
            onSelect={(person) => void assign(person)}
          />
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={assigning}>Cancel</Button>
      </DialogActions>
    </Dialog>
  );
}
