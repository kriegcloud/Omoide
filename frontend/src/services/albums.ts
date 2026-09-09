import { getAlbumMedia } from "./features";
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

/** Album mutations return counts only; snapshot membership before offering Undo. */
export async function getAlbumMemberIds(albumId: number): Promise<Set<number>> {
  const ids = new Set<number>();
  let cursor: string | null = null;
  do {
    const page = await getAlbumMedia(albumId, cursor);
    page.items.forEach((media) => ids.add(media.id));
    cursor = page.next_cursor;
  } while (cursor !== null);
  return ids;
}
