import { API } from "../config";
import type {
  CurationDataset,
  CurationDatasetSummary,
  CurationExportReceipt,
  CurationReviewInput,
  CurationStatus,
  CurationAuthStatus,
  CurationChallenge,
  CurationRegistrationOptions,
  CurationAssertionOptions,
  CurationPresence,
} from "../types/curation";

export class CurationError extends Error {
  constructor(public status: number, public code: string) {
    super(status === 401 || status === 403
      ? "This access credential does not permit that action. Connect with an authorized credential supplied by the local operator."
      : status === 404
        ? "This dataset is unavailable or still review is disabled."
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
    authStatus: (signal?: AbortSignal) => request<CurationAuthStatus>("/auth/status", undefined, signal),
    registrationOptions: (signal?: AbortSignal) => request<CurationChallenge<CurationRegistrationOptions>>("/auth/registration/options", {}, signal),
    register: (input: CurationPresence, signal?: AbortSignal) => request<{ enrolled: true }>("/auth/registration/verify", input, signal),
    list: (signal?: AbortSignal) => request<CurationDatasetSummary[]>("/datasets", undefined, signal),
    get: (id: string, signal?: AbortSignal) => request<CurationDataset>(`/datasets/${encodeURIComponent(id)}`, undefined, signal),
    materialize: (id: string, sourceId: string, revision: number, key: string) => request<CurationDataset>(`/datasets/${encodeURIComponent(id)}/materialize`, {
      source_id: sourceId, expected_revision: revision, idempotency_key: key,
    }),
    caption: (id: string, artifactId: string, text: string, revision: number) => request<CurationDataset>(`/datasets/${encodeURIComponent(id)}/captions`, {
      artifact_id: artifactId, text, expected_revision: revision,
    }),
    reviewOptions: (id: string, input: CurationReviewInput, signal?: AbortSignal) => request<CurationChallenge<CurationAssertionOptions>>(`/datasets/${encodeURIComponent(id)}/review-options`, input, signal),
    review: (id: string, input: CurationReviewInput & { presence?: CurationPresence }, signal?: AbortSignal) => request<CurationDataset>(`/datasets/${encodeURIComponent(id)}/reviews`, input, signal),
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
    generative_ancestry: "Generative changes cannot be accepted in still review",
    generative_disabled: "Generative review is unavailable",
    human_acceptance_required: "Reviewer acceptance is needed",
    no_items: "Prepare a still to begin",
    no_eligible_items: "No stills are ready to export",
    unmaterialized_sources: "Some originals still need to be prepared",
    empty_dataset: "No training stills are available",
    item_review_incomplete: "Some images and captions still need acceptance",
    review_reject: "Rejected stills are not eligible for this export",
    review_defer: "Deferred stills still need review",
    review_stale: "The earlier approval no longer matches this image and caption",
    review_authority_revoked: "An earlier review credential was revoked; review this pair again",
    unsupported_icc: "This image has a color profile that this preview does not support",
    unsupported_color_or_alpha: "This preview requires RGB or grayscale images without transparency",
    unsupported_animation: "Animated images are not supported by this still preview",
    unsupported_format: "This preview supports JPEG, PNG, and still WebP images",
    corrupt_or_unsupported_image: "The image is damaged or unsupported",
    source_unavailable: "The original image is unavailable",
    storage_unavailable: "The image storage is unavailable",
    source_changed: "The source has changed and must be checked before review",
    artifact_changed: "The prepared still changed and needs a new review",
    hash_mismatch: "The image no longer matches the registered source",
    file_too_large: "The image exceeds the allowed file size",
    decoded_image_too_large: "The image exceeds the allowed image dimensions",
    unknown_lineage: "The image origin could not be established",
    invalid_lineage: "The image history needs to be checked",
    holdout_materialization_disabled: "This image is reserved for evaluation and cannot be prepared for training",
    invalid_caption: "Enter a nonempty caption within the caption limit",
    idempotency_conflict: "This request no longer matches its earlier attempt; refresh the review",
    human_presence_required: "Confirm this decision with your registered passkey",
    credential_not_enrolled: "A registered passkey is required; refresh to check enrollment",
    credential_already_enrolled: "This reviewer already has a passkey; refresh to check enrollment",
    credential_already_registered: "This passkey is already registered; ask the local operator to check enrollment",
    presence_challenge_invalid: "This passkey request is no longer valid; refresh and confirm a new decision",
    presence_challenge_used: "This passkey confirmation has already been used; refresh before continuing",
    presence_challenge_expired: "This passkey request expired; refresh and confirm a new decision",
    presence_request_changed: "The decision changed after passkey confirmation; refresh and review the current pair",
    presence_configuration_invalid: "Passkey setup is unavailable; the local operator must check the site configuration",
    presence_configuration_changed: "Passkey site settings changed; refresh before continuing",
    presence_verification_failed: "Passkey verification failed; refresh before confirming this decision again",
    presence_response_invalid: "The passkey response could not be verified; refresh before continuing",
    presence_challenge_limit: "Too many passkey requests are pending; wait before refreshing and trying again",
    presence_not_allowed: "This access credential cannot use passkey confirmation",
    authority_mode_mismatch: "This credential belongs to a different review mode; reconnect with the correct credential",
    production_authority_required: "A production review credential is required",
  };
  return labels[code] ?? "An unresolved review requirement blocks export";
}
