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
  if (!response.ok) throw new Error("Failed to delete selected events");
  return response.json();
};
