import { API } from "../config";
import { Tag } from "../types";
import { CursorPage } from "../types";

export const getTags = async (
  cursor: string | null
): Promise<CursorPage<Tag>> => {
  const params = new URLSearchParams();
  if (cursor) {
    params.append("cursor", cursor);
  }
  const response = await fetch(`${API}/api/tags/?${params.toString()}`);
  if (!response.ok) throw new Error("Failed to fetch tags");
  return response.json();
};

export const getTag = async (id: string): Promise<Tag> => {
  const response = await fetch(`${API}/api/tags/${id}`);
  if (!response.ok) throw new Error(`Failed to load tag (${response.status})`);
  return response.json();
};

export const deleteTagsBulk = async (
  tagIds: number[],
): Promise<{ deleted_ids: number[]; skipped_ids: number[] }> => {
  const response = await fetch(`${API}/api/tags/bulk-delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tag_ids: tagIds }),
  });
  if (!response.ok) throw new Error("Failed to delete selected tags");
  return response.json();
};
