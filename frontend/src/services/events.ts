import { DefinitiveMutationError } from '../stores/optimisticOperations';
import { mutationBus } from "../stores/mutationBus";
import { API } from "../config";
import type { BulkDeleteResult } from "./albums";

export const deleteEventsBulk = async (
  eventIds: number[],
): Promise<BulkDeleteResult> => {
  const response = await fetch(`${API}/api/events/bulk-delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ event_ids: eventIds }),
  });
  if (!response.ok) throw ([401, 403, 422].includes(response.status) ? new DefinitiveMutationError("Failed to delete selected events") : new Error("Failed to delete selected events"));
  const result = await response.json();
  mutationBus.emit({ type: "events:changed", ids: result.deleted_ids });
  return result;
};
