import { API } from "../config";
import {
  AnnotationAttempt,
  AnnotationHealth,
  AnnotationKind,
  MediaAnnotation,
  MediaAnnotationState,
} from "../types";

async function jsonOrError<T>(response: Response, fallback: string): Promise<T> {
  if (response.ok) return response.json();
  let message = fallback;
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (typeof detail === "string") message = detail;
    else if (typeof detail?.message === "string") message = detail.message;
  } catch {
    // Keep the operation-specific fallback when the body is not JSON.
  }
  throw new Error(message);
}

export const getAnnotationHealth = async (): Promise<AnnotationHealth> =>
  jsonOrError(
    await fetch(`${API}/api/annotations/health`),
    "Failed to check the annotation backend"
  );

export const getMediaAnnotations = async (
  mediaId: number
): Promise<MediaAnnotationState> =>
  jsonOrError(
    await fetch(`${API}/api/annotations/media/${mediaId}`),
    "Failed to load annotations"
  );

export const getAnnotationAttempt = async (
  attemptId: string
): Promise<AnnotationAttempt> =>
  jsonOrError(
    await fetch(`${API}/api/annotations/attempts/${attemptId}`),
    "Failed to load raw annotation evidence"
  );

export const startAnnotation = async (
  mediaId: number,
  kind: AnnotationKind
): Promise<AnnotationAttempt> =>
  jsonOrError(
    await fetch(`${API}/api/annotations/media/${mediaId}/attempts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind }),
    }),
    `Failed to start ${kind} generation`
  );

export const cancelAnnotation = async (
  attemptId: string
): Promise<AnnotationAttempt> =>
  jsonOrError(
    await fetch(`${API}/api/annotations/attempts/${attemptId}/cancel`, {
      method: "POST",
    }),
    "Failed to cancel the annotation"
  );

export const retryAnnotation = async (
  attemptId: string
): Promise<AnnotationAttempt> =>
  jsonOrError(
    await fetch(`${API}/api/annotations/attempts/${attemptId}/retry`, {
      method: "POST",
    }),
    "Failed to retry the annotation"
  );

export const createAnnotationRevision = async (
  annotationId: string,
  content: Record<string, unknown>
): Promise<MediaAnnotation> =>
  jsonOrError(
    await fetch(`${API}/api/annotations/annotations/${annotationId}/revisions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }),
    "Failed to save the annotation revision"
  );

export const approveAnnotation = async (
  annotationId: string
): Promise<MediaAnnotation> =>
  jsonOrError(
    await fetch(`${API}/api/annotations/annotations/${annotationId}/approve`, {
      method: "POST",
    }),
    "Failed to approve the annotation"
  );
