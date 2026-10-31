import { API } from "../config";
import type {
  CurationDataset,
  CurationDatasetSummary,
  CurationExportReceipt,
  CurationReviewInput,
  CurationStatus,
} from "../types/curation";

export class CurationError extends Error {
  constructor(public status: number, public code: string) {
    super(status === 401 || status === 403
      ? "This credential does not permit that action. Connect with an authorized fixture credential."
      : status === 404
        ? "This fixture is unavailable or still review is disabled."
        : code === "revision_conflict" || code === "review_hash_conflict"
          ? "The review changed. Refresh before making another decision."
          : status === 409
            ? curationBlockerLabel(code)
          : "The request could not be completed. Refresh to check its current state before retrying.");
    this.name = "CurationError";
  }
}

async function checked(response: Response): Promise<Response> {
  if (response.ok) return response;
  const body = await response.json().catch(() => null);
  throw new CurationError(response.status, typeof body?.detail?.code === "string" ? body.detail.code : "request_failed");
}

export async function getCurationStatus(signal?: AbortSignal): Promise<CurationStatus> {
  const response = await checked(await fetch(`${API}/api/curation/status`, { signal, cache: "no-store" }));
  return response.json();
}

/** The credential belongs only to this in-memory client: never a URL or browser storage. */
export function createCurationClient(credential: string) {
  async function request<T>(path: string, body?: object, signal?: AbortSignal): Promise<T> {
    const response = await checked(await fetch(`${API}/api/curation${path}`, {
      method: body ? "POST" : "GET",
      headers: { Authorization: `Bearer ${credential}`, ...(body ? { "Content-Type": "application/json" } : {}) },
      body: body ? JSON.stringify(body) : undefined,
      cache: "no-store",
      signal,
    }));
    return response.json();
  }

  return {
    list: (signal?: AbortSignal) => request<CurationDatasetSummary[]>("/datasets", undefined, signal),
    get: (id: string, signal?: AbortSignal) => request<CurationDataset>(`/datasets/${encodeURIComponent(id)}`, undefined, signal),
    materialize: (id: string, sourceId: string, revision: number, key: string) => request<CurationDataset>(`/datasets/${encodeURIComponent(id)}/materialize`, {
      source_id: sourceId, expected_revision: revision, idempotency_key: key,
    }),
    caption: (id: string, artifactId: string, text: string, revision: number) => request<CurationDataset>(`/datasets/${encodeURIComponent(id)}/captions`, {
      artifact_id: artifactId, text, expected_revision: revision,
    }),
    review: (id: string, input: CurationReviewInput) => request<CurationDataset>(`/datasets/${encodeURIComponent(id)}/reviews`, input),
    export: (id: string, revision: number, key: string) => request<CurationExportReceipt>(`/datasets/${encodeURIComponent(id)}/exports`, {
      expected_revision: revision, idempotency_key: key,
    }),
    resume: (id: string) => request<CurationExportReceipt>(`/exports/${encodeURIComponent(id)}/resume`, {}),
    receipt: (id: string) => request<CurationExportReceipt>(`/exports/${encodeURIComponent(id)}`),
    async image(kind: "sources" | "artifacts", id: string, sha256: string, signal?: AbortSignal): Promise<Blob> {
      const response = await checked(await fetch(`${API}/api/curation/${kind}/${encodeURIComponent(id)}/content`, {
        headers: { Authorization: `Bearer ${credential}` }, cache: "no-store", signal,
      }));
      const blob = await response.blob();
      const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
      const actual = Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, "0")).join("");
      if (actual !== sha256) throw new Error("The image changed or could not be verified. Refresh this review before continuing.");
      return blob;
    },
  };
}

export type CurationClient = ReturnType<typeof createCurationClient>;

export function curationBlockerLabel(code: string): string {
  const labels: Record<string, string> = {
    unreviewed: "Awaiting review",
    review_required: "Awaiting review",
    missing_caption: "A caption is needed",
    caption_required: "A caption is needed",
    caption_unapproved: "Caption approval is needed",
    acceptance_required: "Acceptance is needed",
    not_accepted: "Acceptance is needed",
    rejected: "Rejected from this draft",
    deferred: "Deferred for another review",
    generative_ancestry: "Generative changes cannot be accepted in this fixture",
    generative_disabled: "Generative review is unavailable",
    human_acceptance_required: "Fixture reviewer acceptance is needed",
    no_items: "Prepare a still to begin",
    no_eligible_items: "No stills are ready to export",
    unmaterialized_sources: "Some original fixtures still need to be prepared",
    empty_dataset: "No training stills are available",
    item_review_incomplete: "Some images and captions still need acceptance",
    review_reject: "Rejected stills are not eligible for this export",
    review_defer: "Deferred stills still need review",
    review_stale: "The earlier approval no longer matches this image and caption",
    review_authority_revoked: "An earlier review credential was revoked; review this pair again",
    unsupported_icc: "This fixture has a color profile that this preview does not support",
    unsupported_color_or_alpha: "This preview requires RGB or grayscale images without transparency",
    unsupported_animation: "Animated images are not supported by this still preview",
    unsupported_format: "This preview supports JPEG, PNG, and still WebP fixtures",
    corrupt_or_unsupported_image: "The fixture image is damaged or unsupported",
    source_unavailable: "The original fixture is unavailable",
    storage_unavailable: "The fixture storage is unavailable",
    source_changed: "The source has changed and must be checked before review",
    artifact_changed: "The prepared still changed and needs a new review",
    hash_mismatch: "The image no longer matches the registered fixture",
    file_too_large: "The fixture exceeds the allowed file size",
    decoded_image_too_large: "The fixture exceeds the allowed image dimensions",
    unknown_lineage: "The image origin could not be established",
    invalid_lineage: "The image history needs to be checked",
    holdout_materialization_disabled: "This fixture is reserved for evaluation and cannot be prepared for training",
    invalid_caption: "Enter a nonempty caption within the fixture caption limit",
    idempotency_conflict: "This request no longer matches its earlier attempt; refresh the review",
  };
  return labels[code] ?? "An unresolved review requirement blocks export";
}
