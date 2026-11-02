import { startOperation, type OperationPatch, DefinitiveMutationError } from "../stores/optimisticOperations";
import type { EntityKey } from "../stores/queryKeys";
import { affectedQueries, revalidateQueries, revalidateUnknownQueries } from "../stores/optimisticOperations";
import { mutationBus } from "../stores/mutationBus";
import { API } from "../config";
import {
  MergeCandidatesPage,
  FaceRead,
  CursorPage,
  SimilarPersonWithDetails,
  Person,
  PersonPairDecision,
  PersonPairDecisionCreate,
  PersonRelationshipGraph,
  SocialLink,
  SocialLinkSuggestion,
  SocialPlatform,
  SocialLinkPreview,
  SocialBrand,
} from "../types";

// Presentation and request validation reject before handlers acquire/write rows.
const peopleMutationError = (response: Response, message: string) =>
  [403, 422].includes(response.status) ? new DefinitiveMutationError(message) : new Error(message);

export interface MergeResult {
  merged_ids: number[];
  skipped_ids: number[];
}

export interface DeletePersonsResult {
  deleted_ids: number[];
  skipped_ids: number[];
}

export interface HidePersonsResult {
  hidden_ids: number[];
  skipped_ids: number[];
}

export interface UnhidePersonsResult {
  unhidden_ids: number[];
  skipped_ids: number[];
}

export interface AddMediaAppearanceResult {
  person_id: number;
  media_id: number;
  added: boolean;
}

export interface PersonMediaBulkResult {
  added_ids?: number[];
  detached_ids?: number[];
  detached_faces?: { id: number; media_id: number }[];
  skipped_ids: number[];
}

export interface SocialLinkCreate {
  platform?: SocialPlatform;
  handle?: string;
  url: string;
  label?: string | null;
  sort_order?: number;
}

const socialLinkError = async (response: Response, fallback: string) => {
  try {
    const body = await response.json();
    return response.status >= 400 && response.status < 500
      ? new DefinitiveMutationError(typeof body.detail === "string" ? body.detail : fallback)
      : new Error(fallback);
  } catch {
    return new Error(fallback);
  }
};

export const getSocialLinks = async (
  personId: number,
  signal?: AbortSignal,
): Promise<SocialLink[]> => {
  const response = await fetch(`${API}/api/person/${personId}/social-links`, {
    signal,
  });
  if (!response.ok) {
    throw await socialLinkError(response, "Failed to load social links");
  }
  return response.json();
};

export const addSocialLink = async (
  personId: number,
  data: SocialLinkCreate,
): Promise<SocialLink> => {
  const response = await fetch(`${API}/api/person/${personId}/social-links`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    throw await socialLinkError(response, "Failed to add social link");
  }
  const result = await response.json();
  mutationBus.emit({ type: "person:changed", ids: [personId] });
  return result;
};

export const deleteSocialLink = async (
  personId: number,
  linkId: number,
): Promise<void> => {
  const response = await fetch(
    `${API}/api/person/${personId}/social-links/${linkId}`,
    { method: "DELETE" },
  );
  if (!response.ok) {
    throw await socialLinkError(response, "Failed to delete social link");
  }
  mutationBus.emit({ type: "person:changed", ids: [personId] });
};


export const previewSocialLink = async (url: string, signal?: AbortSignal): Promise<SocialLinkPreview> => {
  const response = await fetch(`${API}/api/person/social-links/preview`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url }), signal });
  if (!response.ok) throw await socialLinkError(response, "Enter a valid profile URL");
  return response.json();
};
export const editSocialLink = async (personId: number, linkId: number, data: Partial<SocialLinkCreate>): Promise<SocialLink> => {
  const response = await fetch(`${API}/api/person/${personId}/social-links/${linkId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
  if (!response.ok) throw await socialLinkError(response, "Failed to edit social link");
  return response.json();
};
export const reorderSocialLinks = async (personId: number, linkIds: number[]): Promise<SocialLink[]> => {
  const response = await fetch(`${API}/api/person/${personId}/social-links/reorder`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ link_ids: linkIds }) });
  if (!response.ok) throw await socialLinkError(response, "Failed to reorder links");
  return response.json();
};
export const getSocialBrand = async (domain: string, signal?: AbortSignal): Promise<SocialBrand> => {
  const response = await fetch(`${API}/api/social-brands/${encodeURIComponent(domain)}`, { signal });
  if (!response.ok) throw await socialLinkError(response, "Failed to check icon");
  return response.json();
};
export const updateSocialBrand = async (domain: string, file?: File): Promise<SocialBrand> => {
  const response = await fetch(`${API}/api/social-brands/${encodeURIComponent(domain)}/${file ? "icon" : "retry"}`, { method: "POST", ...(file ? { body: file, headers: { "Content-Type": file.type || "application/octet-stream" } } : {}) });
  if (!response.ok) throw await socialLinkError(response, "Failed to update icon");
  return response.json();
};

export const getSocialLinkSuggestions = async (
  personId: number,
  signal?: AbortSignal,
): Promise<SocialLinkSuggestion[]> => {
  const response = await fetch(
    `${API}/api/person/${personId}/social-links/suggestions`,
    { signal },
  );
  if (!response.ok) {
    throw await socialLinkError(response, "Failed to load link suggestions");
  }
  return response.json();
};

export interface PersonMediaExportResult {
  mode: "copy" | "move";
  completed: number;
  skipped: { filename: string; reason: string }[];
}

export const exportPersonMedia = async (
  personId: number,
  destinationPath: string,
  mode: "copy" | "move"
): Promise<import("../types").Task> => {
  const res = await fetch(`${API}/api/person/${personId}/export-media`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ destination_path: destinationPath, mode }),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error?.detail || `Failed to ${mode} media`);
  }
  return res.json();
};

export const updatePerson = async (
  personId: number,
  data: {
    name?: string;
    profile_face_id?: number;
    gender?: "female" | "male" | null;
  }
): Promise<Person> => {
  const res = await fetch(`${API}/api/person/${personId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw peopleMutationError(res, "Failed to update person");
  const result = await res.json();
  mutationBus.emit({ type: "person:changed", ids: [personId] });
  return result;
};

export const deletePerson = async (personId: number) => {
  const res = await fetch(`${API}/api/person/${personId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error("Failed to delete person");
  mutationBus.emit({ type: "person:changed", ids: [personId] });
};

export const deletePersonsBulk = async (
  personIds: number[],
): Promise<DeletePersonsResult> => {
  const res = await fetch(`${API}/api/person/bulk-delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ person_ids: personIds }),
  });
  if (!res.ok) throw peopleMutationError(res, "Failed to delete selected people");
  const result = await res.json();
  mutationBus.emit({ type: "person:changed", ids: result.deleted_ids });
  return result;
};

export const hidePerson = async (personId: number): Promise<Person> => {
  const res = await fetch(`${API}/api/person/${personId}/hide`, {
    method: "POST",
  });
  if (!res.ok) throw peopleMutationError(res, "Failed to hide person");
  const result = await res.json();
  mutationBus.emit({ type: "person:changed", ids: [personId] });
  return result;
};

export const unhidePerson = async (personId: number): Promise<Person> => {
  const res = await fetch(`${API}/api/person/${personId}/unhide`, {
    method: "POST",
  });
  if (!res.ok) throw peopleMutationError(res, "Failed to unhide person");
  const result = await res.json();
  mutationBus.emit({ type: "person:changed", ids: [personId] });
  return result;
};

export const hidePersonsBulk = async (
  personIds: number[],
): Promise<HidePersonsResult> => {
  const res = await fetch(`${API}/api/person/bulk-hide`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ person_ids: personIds }),
  });
  if (!res.ok) throw peopleMutationError(res, "Failed to hide selected people");
  const result = await res.json();
  mutationBus.emit({ type: "person:changed", ids: result.hidden_ids });
  return result;
};

export const unhidePersonsBulk = async (
  personIds: number[],
): Promise<UnhidePersonsResult> => {
  const res = await fetch(`${API}/api/person/bulk-unhide`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ person_ids: personIds }),
  });
  if (!res.ok) throw peopleMutationError(res, "Failed to unhide selected people");
  const result = await res.json();
  mutationBus.emit({ type: "person:changed", ids: result.unhidden_ids });
  return result;
};

export const getMergeCandidates = async (
  minSimilarity = 80,
  limit = 50,
  signal?: AbortSignal,
): Promise<MergeCandidatesPage> => {
  const params = new URLSearchParams({
    min_similarity: minSimilarity.toString(),
    limit: limit.toString(),
  });
  const res = await fetch(`${API}/api/persons/merge-candidates?${params}`, {
    signal,
  });
  if (!res.ok) throw new Error("Failed to load merge candidates");
  return res.json();
};

export const createPairDecision = async (
  data: PersonPairDecisionCreate,
): Promise<PersonPairDecision> => {
  const res = await fetch(`${API}/api/persons/pair-decisions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to save pair decision");
  const result = await res.json();
  mutationBus.emit({ type: "person:changed", ids: [] });
  return result;
};

export const deletePairDecision = async (id: number): Promise<void> => {
  const res = await fetch(`${API}/api/persons/pair-decisions/${id}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error("Failed to delete pair decision");
  mutationBus.emit({ type: "person:changed", ids: [] });
};

export const mergePersons = async (sourceId: number, targetId: number) => {
  const res = await fetch(`${API}/api/person/merge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_id: sourceId, target_id: targetId }),
  });
  if (!res.ok) throw peopleMutationError(res, "Failed to merge persons");
  mutationBus.emit({ type: "person:changed", ids: [sourceId, targetId] });
};

export const mergeMultiplePersons = async (
  targetId: number,
  sourceIds: number[]
): Promise<MergeResult> => {
  const res = await fetch(`${API}/api/person/${targetId}/merge-multiple`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_ids: sourceIds }),
  });
  if (!res.ok) throw peopleMutationError(res, "Failed to merge selected persons");
  const result = await res.json();
  mutationBus.emit({ type: "person:changed", ids: [targetId, ...result.merged_ids] });
  return result;
};

export const autoMergeSimilarPersons = async (
  personId: number
): Promise<MergeResult> => {
  const res = await fetch(`${API}/api/person/${personId}/merge-similar`, {
    method: "POST",
  });
  if (!res.ok) throw new Error("Failed to auto-merge similar persons");
  const result = await res.json();
  mutationBus.emit({ type: "person:changed", ids: [personId, ...result.merged_ids] });
  return result;
};

export const searchPersonsByName = async (
  name: string,
  signal?: AbortSignal
): Promise<Person[]> => {
  const res = await fetch(
    `${API}/api/person/?name=${encodeURIComponent(name)}`,
    { signal }
  );
  if (!res.ok) throw new Error("Failed to search persons");
  const data = await res.json();
  return data.items;
};

export const attachMediaToPersonBulk = async (
  personId: number,
  mediaIds: number[]
): Promise<PersonMediaBulkResult> => {
  const res = await fetch(`${API}/api/person/${personId}/media/bulk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ media_ids: mediaIds }),
  });
  if (!res.ok) throw peopleMutationError(res, "Failed to attach selected media");
  const result = await res.json();
  mutationBus.emit({ type: "person:changed", ids: [personId] });
  return result;
};

export const detachMediaFromPersonBulk = async (
  personId: number,
  mediaIds: number[]
): Promise<PersonMediaBulkResult> => {
  const res = await fetch(`${API}/api/person/${personId}/media/bulk-detach`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ media_ids: mediaIds }),
  });
  if (!res.ok) throw new Error("Failed to detach selected media");
  const result = await res.json();
  mutationBus.emit({ type: "person:changed", ids: [personId] });
  mutationBus.emit({ type: "face:detached", faceIds: (result.detached_faces ?? []).map((face: { id: number }) => face.id), personId });
  return result;
};

export const reassignMediaToPerson = async (
  sourcePersonId: number,
  mediaId: number,
  targetPersonId: number
): Promise<{ reassigned: boolean }> => {
  const res = await fetch(
    `${API}/api/person/${sourcePersonId}/media/${mediaId}/reassign`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_person_id: targetPersonId }),
    }
  );
  if (!res.ok) throw peopleMutationError(res, "Failed to assign media to person");
  const result = await res.json();
  mutationBus.emit({ type: "person:changed", ids: [sourcePersonId, targetPersonId] });
  return result;
};

export const getSuggestedFaces = async (
  personId: number,
  limit: number = 100,
  signal?: AbortSignal
): Promise<FaceRead[]> => {
  const res = await fetch(
    `${API}/api/person/${personId}/suggest-faces?limit=${limit}`,
    { signal }
  );
  if (!res.ok) throw new Error("Failed to fetch suggested faces");
  return res.json();
};

export const getSimilarPersons = async (
  personId: number,
  signal?: AbortSignal
): Promise<SimilarPersonWithDetails[]> => {
  const res = await fetch(`${API}/api/person/${personId}/similarities`, {
    signal,
  });
  if (!res.ok) throw new Error("Failed to fetch similar persons");
  return res.json();
};

export const getPersonRelationshipGraph = async (
  personId: number,
  depth: number,
  maxNodes = 100,
  signal?: AbortSignal
): Promise<PersonRelationshipGraph> => {
  const params = new URLSearchParams({
    depth: depth.toString(),
    max_nodes: maxNodes.toString(),
  });
  const res = await fetch(
    `${API}/api/person/${personId}/relationships?${params.toString()}`,
    { signal }
  );
  if (!res.ok) {
    throw new Error("Failed to fetch person relationship graph");
  }
  return res.json();
};

export const getPersonFaces = async (
  personId: number,
  cursor: string | null,
  limit: number,
  sortBy: string = "id_desc",
  mediaId?: number
): Promise<CursorPage<FaceRead>> => {
  const params = new URLSearchParams({
    limit: limit.toString(),
    sort_by: sortBy,
  });
  if (cursor) params.append("cursor", cursor);
  if (mediaId != null) params.append("media_id", mediaId.toString());
  const res = await fetch(
    `${API}/api/person/${personId}/faces?${params.toString()}`
  );
  if (!res.ok) throw new Error("Failed to fetch person faces");
  return res.json();
};

export const detachMediaFromPerson = async (
  personId: number,
  mediaId: number
): Promise<{ message: string; detached_faces: { id: number; media_id: number }[] }> => {
  const res = await fetch(
    `${API}/api/person/${personId}/media/${mediaId}/detach`,
    { method: "POST" }
  );
  if (!res.ok) throw new Error("Failed to detach media from person");
  const result = await res.json();
  mutationBus.emit({ type: "person:changed", ids: [personId] });
  mutationBus.emit({ type: "face:detached", faceIds: result.detached_faces.map((face: { id: number }) => face.id), personId });
  return result;
};

export const setProfileFace = async (faceId: number, personId: number) => {
  return updatePerson(personId, { profile_face_id: faceId });
};

export const autoSelectProfileFace = async (personId: number): Promise<Person> => {
  const res = await fetch(`${API}/api/person/${personId}/profile_face/auto`, {
    method: "POST",
  });
  if (!res.ok) {
    throw peopleMutationError(res, "Failed to auto-select profile image");
  }
  const result = await res.json();
  mutationBus.emit({ type: "person:changed", ids: [personId] });
  return result;
};

export const addMediaAppearanceToPerson = async (
  personId: number,
  mediaId: number
): Promise<AddMediaAppearanceResult> => {
  const res = await fetch(`${API}/api/person/${personId}/media`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ media_id: mediaId }),
  });
  if (!res.ok) {
    throw new Error("Failed to add media appearance to person");
  }
  const result = await res.json();
  mutationBus.emit({ type: "person:changed", ids: [personId] });
  return result;
};

export const setPersonFavorite = async (personId: number, isFavorite: boolean): Promise<void> => {
 const response = await fetch(`${API}/api/person/${personId}/favorite`, {method: "PATCH", headers: {"Content-Type": "application/json"}, body: JSON.stringify({is_favorite: isFavorite})});
 if (!response.ok) throw peopleMutationError(response, "Failed to update favorite");
 mutationBus.emit({type: "person:changed", ids: [personId]});
};

/** One operation owns each pending intent. Legacy transport failures remain unknown;
 * these endpoints have no durable operation lookup and must never be replayed. */
export function startPeopleMutation<R>(label: string, personIds: readonly number[], request: () => Promise<R>, options: {
  query?: string;
  resources?: readonly number[];
  entityResources?: readonly EntityKey[];
  applied?: (result: R) => readonly number[];
  patches?: readonly OperationPatch[];
  responsePatches?: (result: R) => readonly OperationPatch[];
  refresh?: () => Promise<void>;
  refreshUnknown?: () => Promise<void>;
} = {}) {
  const ids = [...new Set(personIds)];
  const keys = ids.map(id => `person:${id}` as const);
  const operationId = crypto.randomUUID();
  return startOperation({
    id: operationId, label, input: { ids },
    resources: [...keys, ...(options.resources ?? []).map(id => `person:${id}` as const), ...(options.entityResources ?? [])],
    patches: options.patches ?? keys.map(key => options.query
      ? { kind: "remove" as const, key, query: options.query }
      : { kind: "derived" as const, key, field: "mutation" }),
    request,
    receipt: result => {
      const applied = new Set(options.applied ? options.applied(result) : ids);
      if ([...applied].some(id => !ids.includes(id))) throw new Error("Unexpected person in mutation result");
      const skipped = ids.filter(id => !applied.has(id)).map(id => ({ key: `person:${id}`, reason: "not_applied" }));
      return { operation_id: operationId, state: skipped.length ? applied.size ? "partial" : "failed" : "succeeded",
        applied: [...applied].map(id => `person:${id}`), skipped, failed: [], unknown: [] };
    },
    responsePatches: options.responsePatches,
    reconcileUnknownAfterRefresh: true,
    refresh: options.refresh ?? (() => revalidateQueries(affectedQueries(keys))),
    refreshUnknown: options.refreshUnknown ?? (() => revalidateUnknownQueries([...affectedQueries([...keys, ...(options.resources ?? []).map(id => `person:${id}` as const), ...(options.entityResources ?? [])]), ...(options.query ? [options.query] : [])])),
  });
}
