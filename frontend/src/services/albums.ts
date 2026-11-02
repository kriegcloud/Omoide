import { DefinitiveMutationError } from "../stores/optimisticOperations";
import { mutationBus } from "../stores/mutationBus";
import { API } from "../config";

export interface BulkDeleteResult {
  deleted_ids: number[];
  skipped_ids: number[];
}

export const deleteAlbumsBulk = async (
  albumIds: number[],
): Promise<BulkDeleteResult> => {
  const response = await fetch(`${API}/api/albums/bulk-delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ album_ids: albumIds }),
  });
  if (!response.ok) {
    if ([403, 422].includes(response.status)) throw new DefinitiveMutationError("Failed to delete selected albums");
    throw new Error("Failed to delete selected albums");
  }
  const result = await response.json();
  mutationBus.emit({ type: "album:changed", ids: result.deleted_ids });
  return result;
};
