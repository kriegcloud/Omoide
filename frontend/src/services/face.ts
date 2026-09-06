import { API } from "../config";
import { CursorPage, FaceRead } from "../types";

export const getOrphanFaces = async (
  cursor: string | null
): Promise<CursorPage<FaceRead>> => {
  const params = new URLSearchParams();
  if (cursor) {
    params.append("cursor", cursor);
  }
  const response = await fetch(`${API}/api/faces/orphans?${params.toString()}`);
  if (!response.ok) throw new Error("Failed to fetch orphan faces");
  return response.json();
};

export const getOrphanFaceCount = async (): Promise<number> => {
  const response = await fetch(`${API}/api/faces/orphans/count`);
  if (!response.ok) throw new Error("Failed to fetch orphan face count");
  const data: { count: number } = await response.json();
  return data.count;
};
