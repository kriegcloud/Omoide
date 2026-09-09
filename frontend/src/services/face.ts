import { API } from "../config";
import {
  AssignSuggestedFacesResult,
  CursorPage,
  FaceRead,
  OrphanFaceSuggestion,
  SuggestedFaceAssignment,
} from "../types";

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

export const getOrphanFaceSuggestions = async (
  cursor: string | null = null,
  limit = 48,
  minScore = 0,
): Promise<CursorPage<OrphanFaceSuggestion>> => {
  const params = new URLSearchParams({
    limit: limit.toString(),
    min_score: minScore.toString(),
  });
  if (cursor) params.append("cursor", cursor);
  const response = await fetch(
    `${API}/api/faces/orphans/suggestions?${params.toString()}`,
  );
  if (!response.ok) throw new Error("Failed to fetch orphan face suggestions");
  return response.json();
};

export const assignSuggestedFaces = async (
  assignments: SuggestedFaceAssignment[],
  source: "suggestion" = "suggestion",
): Promise<AssignSuggestedFacesResult> => {
  const response = await fetch(`${API}/api/faces/assign-suggested`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ assignments, source }),
  });
  if (!response.ok) throw new Error("Failed to assign suggested faces");
  return response.json();
};
