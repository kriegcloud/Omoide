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
  if (!response.ok) throw new Error("Failed to delete selected albums");
  return response.json();
};
