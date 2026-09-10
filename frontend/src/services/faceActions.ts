import { API } from "../config";
import { FaceAssign, FaceAssignSource, Person } from "../types";

export const assignFace = async (
  faceIds: number[],
  personId: number,
  source: FaceAssignSource = "manual",
) => {
  const body: FaceAssign = { face_ids: faceIds, person_id: personId, source };
  const res = await fetch(`${API}/api/faces/assign`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Assign failed: ${res.status}`);
};

export const createPersonFromFaces = async (
  faceIds: number[],
  name?: string
): Promise<Person> => {
  const res = await fetch(`${API}/api/faces/create_person`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ face_ids: faceIds, name: name }),
  });
  if (!res.ok) throw new Error(`Create person failed: ${res.status}`);
  const json = await res.json();
  return (json as any).person ?? (json as any);
};

export const deleteFace = async (faceIds: number[]) => {
  const params = new URLSearchParams();
  faceIds.forEach((id) => params.append("face_ids", id.toString()));
  const res = await fetch(`${API}/api/faces/?${params.toString()}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Delete failed: ${res.status}`);
};

export const detachFace = async (faceIds: number[]) => {
  const res = await fetch(`${API}/api/faces/detach`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ face_ids: faceIds }),
  });
  if (!res.ok) throw new Error(`Detach failed: ${res.status}`);
};

export const deleteAllOrphanFaces = async (): Promise<{ deleted: number }> => {
  const response = await fetch(`${API}/api/faces/orphans`, { method: "DELETE" });
  if (!response.ok) {
    const error: { detail?: unknown } = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === "string" ? error.detail : "Failed to delete all unassigned faces");
  }
  return response.json();
};
