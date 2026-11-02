import { startOperation, DefinitiveMutationError, isDefinitiveMutationError, type OperationPatch } from "../stores/optimisticOperations";
import { mutationBus } from "../stores/mutationBus";
import { API } from "../config";
import type {
  DatasetExport,
  DatasetExportLayout,
  DatasetItem,
  DatasetItemPage,
  DatasetAnalysis,
  AutoSelectInput,
  AutoSelectResult,
  TrainingDataset,
  CropAspect,
  CropFraming,
  DatasetBatchCropResult,
  TrainingRun,
  TrainingRunInput,
  TrainingHealth,
  TrainingPreset,
  TrainingSample,
  RunLikeness,
  DatasetCaptionFilter,
  DatasetCaptionPage,
  Task,
  CompositionGap,
  DatasetTriageFilter,
  DatasetTriagePage,
  FrameMiningCandidates,
  DatasetDedupeInput,
  DatasetDedupeResult,
  EvalBatch,
  EvalBatchInput,
} from "../types";
import type { EditOp, FilerobotDesignState } from "../utils/editorOps";

type DatasetInput = Partial<Omit<TrainingDataset, "id" | "created_at" | "updated_at" | "item_count" | "included_count">> & { name: string };

// Server-side authority denials arrive as `{ detail: { code } }`; readable text
// keeps them from rendering as "[object Object]" in page error banners.
const DETAIL_MESSAGES: Record<string, string> = {
  legacy_human_authority_unavailable:
    "Legacy review stamping is disabled while production curation authority is active. "
    + "Review this item in Curation and confirm with your passkey instead.",
  request_too_large: "Request body is too large.",
};

export function describeApiDetail(detail: unknown, status: number): string {
  if (typeof detail === "string" && detail.length > 0) return detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const { code, message } = detail as { code?: unknown; message?: unknown };
    if (typeof code === "string") return DETAIL_MESSAGES[code] ?? `Request failed (${code})`;
    if (typeof message === "string" && message.length > 0) return message;
  }
  if (Array.isArray(detail)) {
    const messages = detail
      .map((entry) => (entry && typeof entry === "object" ? (entry as { msg?: unknown }).msg : undefined))
      .filter((msg): msg is string => typeof msg === "string");
    if (messages.length > 0) return messages.join("; ");
  }
  return `Request failed (${status})`;
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const message = describeApiDetail(body?.detail, response.status);
    if ([400, 401, 403, 404, 422].includes(response.status)) throw new DefinitiveMutationError(message);
    throw new Error(message);
  }
  return response.json();
}

export const getDatasets = () => fetch(`${API}/api/datasets`).then(json<TrainingDataset[]>);
export const getDataset = (id: number) => fetch(`${API}/api/datasets/${id}`).then(json<TrainingDataset>);
const transportCreateDataset = (input: DatasetInput) => fetch(`${API}/api/datasets`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
}).then(json<TrainingDataset>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [result.id] });
    return result;
  });
const transportUpdateDataset = (id: number, input: Partial<TrainingDataset>) => fetch(`${API}/api/datasets/${id}`, {
  method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
}).then(json<TrainingDataset>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [id] });
    return result;
  });
const transportDeleteDataset = async (id: number): Promise<void> => {
  const response = await fetch(`${API}/api/datasets/${id}`, { method: "DELETE" });
  if (!response.ok) {
    const message = `Failed to delete dataset (${response.status})`;
    if ([400, 401, 403, 404, 422].includes(response.status)) throw new DefinitiveMutationError(message);
    throw new Error(message);
  }
  mutationBus.emit({ type: "dataset:changed", ids: [id] });
};
const transportAddDatasetItems = (id: number, mediaIds: number[]) => fetch(`${API}/api/datasets/${id}/items`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ media_ids: mediaIds }),
}).then(json<{ added_ids: number[]; skipped_ids: number[] }>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [id] });
    return result;
  });
const transportRemoveDatasetItems = (id: number, mediaIds: number[]) => fetch(`${API}/api/datasets/${id}/items`, {
  method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ media_ids: mediaIds }),
}).then(json<{ added_ids: number[]; skipped_ids: number[] }>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [id] });
    return result;
  });
export const getDatasetItems = (id: number, cursor?: string | null, sort = "position") => {
  const params = new URLSearchParams({ limit: "500" });
  if (cursor) params.set("cursor", cursor);
  params.set("sort", sort);
  return fetch(`${API}/api/datasets/${id}/items?${params}`).then(json<DatasetItemPage>);
};
const transportUpdateDatasetItem = (datasetId: number, itemId: number, input: {
  caption_override?: string | null; edit_ops?: EditOp[] | null; edit_design_state?: FilerobotDesignState | null;
  excluded?: boolean; excluded_reason?: "duplicate" | "burst" | "manual" | "quality" | null;
  weight?: number; position?: number; reviewed_at?: string | null;
}) => fetch(`${API}/api/datasets/${datasetId}/items/${itemId}`, {
  method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
}).then(json<DatasetItem>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [datasetId] });
    return result;
  });
const transportReviewDatasetItem = (datasetId: number, itemId: number) => fetch(
  `${API}/api/datasets/${datasetId}/items/${itemId}/review`,
  { method: "POST" },
).then(json<DatasetItem>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [datasetId] });
    return result;
  });
export const getDatasetTriage = (
  id: number,
  filter: DatasetTriageFilter,
  cursor?: string | null,
) => {
  const params = new URLSearchParams({ filter, limit: "50" });
  if (cursor) params.set("cursor", cursor);
  return fetch(`${API}/api/datasets/${id}/triage?${params}`).then(
    json<DatasetTriagePage>,
  );
};
const transportCreateDatasetExport = (id: number, layout?: DatasetExportLayout) => fetch(`${API}/api/datasets/${id}/export`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ layout }),
}).then(json<DatasetExport>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [id] });
    return result;
  });
export const getDatasetExports = (id: number) => fetch(`${API}/api/datasets/${id}/exports`).then(json<DatasetExport[]>);
export const getTrainingRuns = (id: number) => fetch(`${API}/api/datasets/${id}/runs`).then(json<TrainingRun[]>);
export const getTrainingHealth = () => fetch(`${API}/api/datasets/training/health`).then(json<TrainingHealth>);
export const getTrainingPresets = () => fetch(`${API}/api/datasets/training/presets`).then(json<TrainingPreset[]>);
const transportCreateTrainingRun = (id: number, input: TrainingRunInput) => fetch(`${API}/api/datasets/${id}/train`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
}).then(json<TrainingRun>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [id] });
    return result;
  });
const transportCancelTrainingRun = (runId: number) => fetch(`${API}/api/datasets/runs/${runId}/cancel`, {
  method: "POST",
}).then(json<TrainingRun>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [] });
    return result;
  });
export const getTrainingSamples = (runId: number) => fetch(`${API}/api/datasets/runs/${runId}/samples`).then(json<TrainingSample[]>);
export const getTrainingRunLikeness = (runId: number) => fetch(`${API}/api/datasets/runs/${runId}/likeness`).then(json<RunLikeness>);
export const getDatasetRunLikeness = (id: number, runIds?: number[]) => {
  const query = runIds?.length ? `?run_ids=${runIds.join(",")}` : "";
  return fetch(`${API}/api/datasets/${id}/runs/likeness${query}`).then(json<RunLikeness[]>);
};
const transportRescoreTrainingRun = (runId: number) => fetch(`${API}/api/datasets/runs/${runId}/rescore`, {
  method: "POST",
}).then(json<{ queued: number }>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [] });
    return result;
  });
export const trainingSampleImageUrl = (runId: number, sampleId: number) => `${API}/api/datasets/runs/${runId}/samples/${sampleId}/image`;
const transportCreateEvalBatch = (runId: number, input: EvalBatchInput) => fetch(`${API}/api/datasets/runs/${runId}/eval`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
}).then(json<EvalBatch>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [] });
    return result;
  });
export const getEvalBatches = (runId: number) => fetch(`${API}/api/datasets/runs/${runId}/evals`).then(json<EvalBatch[]>);
export const getEvalBatch = (batchId: number) => fetch(`${API}/api/datasets/evals/${batchId}`).then(json<EvalBatch>);
const transportCancelEvalBatch = (batchId: number) => fetch(`${API}/api/datasets/evals/${batchId}/cancel`, { method: "POST" }).then(json<EvalBatch>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [] });
    return result;
  });
export const evalSampleImageUrl = (batchId: number, sampleId: number) => `${API}/api/datasets/evals/${batchId}/samples/${sampleId}/image`;
const transportCreateDatasetFromPerson = (personId: number) => fetch(`${API}/api/datasets/from-person/${personId}`, { method: "POST" }).then(json<TrainingDataset>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [result.id] });
    return result;
  });
export const datasetManifestUrl = (exportId: number) => `${API}/api/datasets/exports/${exportId}/manifest`;
export const getDatasetAnalysis = (id: number) => fetch(`${API}/api/datasets/${id}/analysis`).then(json<DatasetAnalysis>);
const transportDedupeDataset = (id: number, input: DatasetDedupeInput) => fetch(`${API}/api/datasets/${id}/dedupe`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
}).then(json<DatasetDedupeResult>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [id] });
    return result;
  });
const transportReincludeDatasetItems = (id: number, reason?: "burst" | "duplicate") => fetch(`${API}/api/datasets/${id}/items/reinclude`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason }),
}).then(json<{ included: number }>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [id] });
    return result;
  });
export const getDatasetGaps = (id: number) => fetch(`${API}/api/datasets/${id}/gaps`).then(json<CompositionGap[]>);
const transportFillDatasetGaps = (id: number, input: { max_add: number; dimensions?: string[] }) => fetch(`${API}/api/datasets/${id}/fill-gaps`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
}).then(json<{ added_ids: number[] }>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [id] });
    return result;
  });
const transportBackfillDatasetPose = (id: number) => fetch(`${API}/api/datasets/${id}/pose-backfill`, { method: "POST" }).then(json<{ id: string }>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [id] });
    return result;
  });
export const getFrameMiningCandidates = (
  id: number,
  input: { video_media_id?: number; min_face_px?: number; fps?: number; max_candidates?: number } = {},
) => {
  const params = new URLSearchParams();
  Object.entries(input).forEach(([key, value]) => {
    if (value != null) params.set(key, String(value));
  });
  const query = params.size ? `?${params}` : "";
  return fetch(`${API}/api/datasets/${id}/mine-frames/candidates${query}`).then(json<FrameMiningCandidates>);
};
const transportMineDatasetFrames = (id: number, input: {
  video_media_ids?: number[];
  max_per_video: number;
  min_face_px: number;
  fps: number;
  selected_timestamps_ms?: Record<number, number[]>;
}) => fetch(`${API}/api/datasets/${id}/mine-frames`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
}).then(json<Task>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [id] });
    return result;
  });
const transportAutoSelectDataset = (id: number, input: AutoSelectInput) => fetch(`${API}/api/datasets/${id}/auto-select`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
}).then(json<AutoSelectResult>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [id] });
    return result;
  });
const transportBuildRegularizationDataset = (id: number, input: { target_count: number; gender?: string }) => fetch(`${API}/api/datasets/${id}/regularization`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
}).then(json<TrainingDataset>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [result.id] });
    return result;
  });
const transportBatchCropDatasetItems = (id: number, input: {
  item_ids?: number[];
  framing: CropFraming;
  aspect: CropAspect;
  overwrite_existing_ops: boolean;
}) => fetch(`${API}/api/datasets/${id}/items/batch-crop`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
}).then(json<DatasetBatchCropResult>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [id] });
    return result;
  });

export const getDatasetCaptions = (
  id: number,
  filter: DatasetCaptionFilter,
  cursor?: string | null,
  signal?: AbortSignal,
) => {
  const params = new URLSearchParams({ filter, limit: "100" });
  if (cursor) params.set("cursor", cursor);
  return fetch(`${API}/api/datasets/${id}/captions?${params}`, {signal}).then(
    json<DatasetCaptionPage>,
  );
};

const transportUpdateDatasetCaption = (
  datasetId: number,
  itemId: number,
  text: string,
) => fetch(`${API}/api/datasets/${datasetId}/items/${itemId}/caption`, {
  method: "PATCH",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ text }),
}).then(json<DatasetItem>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [datasetId] });
    return result;
  });

const transportMarkDatasetCaptionReviewed = (
  datasetId: number,
  itemId: number,
) => fetch(`${API}/api/datasets/${datasetId}/items/${itemId}/caption/reviewed`, {
  method: "POST",
}).then(json<{ item_id: number; caption_reviewed_at: string }>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [datasetId] });
    return result;
  });

const transportGenerateDatasetCaptions = (id: number, onlyMissing = true) =>
  fetch(`${API}/api/datasets/${id}/captions/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ only_missing: onlyMissing }),
  }).then(json<Task>).then((result) => {
    mutationBus.emit({ type: "dataset:changed", ids: [id] });
    return result;
  });


/** Legacy dataset writes have no durable operation lookup. Unknown results are
 * resolved with reads and never replayed. Item keys are scoped to their dataset. */
function datasetWrite<T>(label: string, datasetId: number | undefined, request: () => Promise<T>, patches: readonly OperationPatch[] = [], applied?: (result: T) => readonly number[], feedback?: "job", responsePatches?: (result: T) => readonly OperationPatch[]): Promise<T> {
  const key = `dataset:${datasetId ?? "collection"}` as const;
  let result: T;
  let failure: unknown;
  const refreshUnknown = async () => {
    // A successful authoritative read releases the queue even after navigation.
    if (datasetId != null) await Promise.all([getDataset(datasetId), getDatasetItems(datasetId)]); else await getDatasets();
    mutationBus.emit({ type: "dataset:changed", ids: datasetId == null ? [] : [datasetId] });
  };
  const operationId = crypto.randomUUID();
  const handle = startOperation({ id: operationId, label, feedback, input: {}, resources: [key], patches: patches.length ? patches : [{ kind: "derived", key, field: feedback ? "submission" : "mutation" }], responsePatches,
    request: async () => { try { result = await request(); return result; } catch (error) { failure = error; throw error; } },
    receipt: applied ? value => {
      const ids = new Set(applied(value));
      const accepted = patches.filter(patch => ids.has(Number(patch.key.split("/media/")[1]))).map(patch => patch.key);
      const skipped = patches.filter(patch => !accepted.includes(patch.key)).map(patch => ({ key: patch.key, reason: "not_applied" }));
      return { operation_id: operationId, state: skipped.length ? accepted.length ? "partial" : "failed" : "succeeded", applied: accepted, skipped, failed: [], unknown: [] };
    } : undefined,
    refreshUnknown, reconcileUnknownAfterRefresh: true,
    refresh: async () => { mutationBus.emit({ type: "dataset:changed", ids: datasetId == null ? [] : [datasetId] }); },
  });
  return handle.done.then(record => {
    if (record.state === "rejected" || record.state === "unknown") throw failure ?? new Error(record.error ?? "Dataset write could not be confirmed");
    return result;
  });
}
let provisionalDatasetId = -1;
export const createDataset = (...args: Parameters<typeof transportCreateDataset>): ReturnType<typeof transportCreateDataset> => {
  const snapshot = structuredClone(args);
  const id = provisionalDatasetId--;
  const key = `dataset:${id}` as const;
  return datasetWrite("Create dataset", undefined, () => transportCreateDataset(...snapshot), [{ kind: "insert", key, query: "datasets", position: "start",
    item: { id, name: snapshot[0].name, trigger_word: snapshot[0].trigger_word ?? "", class_token: snapshot[0].class_token ?? "person", included_count: 0, item_count: 0 } }],
    undefined, undefined, () => [{ kind: "remove", key, query: "datasets" }]);
};
export const updateDataset = (...args: Parameters<typeof transportUpdateDataset>): ReturnType<typeof transportUpdateDataset> => { const snapshot = structuredClone(args); return datasetWrite("Update dataset", snapshot[0], () => transportUpdateDataset(...snapshot), [{ kind: "fields", key: `dataset:${snapshot[0]}`, values: snapshot[1] }]) };
export const deleteDataset = (...args: Parameters<typeof transportDeleteDataset>): ReturnType<typeof transportDeleteDataset> => { const snapshot = structuredClone(args); return datasetWrite("Delete dataset", snapshot[0], () => transportDeleteDataset(...snapshot), [{ kind: "remove", key: `dataset:${snapshot[0]}` }]) };
export const addDatasetItems = (...args: Parameters<typeof transportAddDatasetItems>): ReturnType<typeof transportAddDatasetItems> => { const snapshot = structuredClone(args); return datasetWrite("Add dataset items", snapshot[0], () => transportAddDatasetItems(...snapshot) .catch(error => { if (isDefinitiveMutationError(error)) throw new Error(error.message); throw error; }), []) };
export const removeDatasetItems = (...args: Parameters<typeof transportRemoveDatasetItems>): ReturnType<typeof transportRemoveDatasetItems> => { const snapshot = structuredClone(args); return datasetWrite("Remove dataset items", snapshot[0], () => transportRemoveDatasetItems(...snapshot) .catch(error => { if (isDefinitiveMutationError(error)) throw new Error(error.message); throw error; }), snapshot[1].map(mediaId => ({ kind: "remove", key: `dataset:${snapshot[0]}/media/${mediaId}` })), result => result.added_ids) };
export const updateDatasetItem = (...args: Parameters<typeof transportUpdateDatasetItem>): ReturnType<typeof transportUpdateDatasetItem> => { const snapshot = structuredClone(args); return datasetWrite("Update dataset item", snapshot[0], () => transportUpdateDatasetItem(...snapshot), [{ kind: "fields", key: `dataset:${snapshot[0]}/item/${snapshot[1]}`, values: snapshot[2] }]) };
export const reviewDatasetItem = (...args: Parameters<typeof transportReviewDatasetItem>): ReturnType<typeof transportReviewDatasetItem> => { const snapshot = structuredClone(args); return datasetWrite("Review dataset item", snapshot[0], () => transportReviewDatasetItem(...snapshot), []) };
export const createDatasetExport = (...args: Parameters<typeof transportCreateDatasetExport>): ReturnType<typeof transportCreateDatasetExport> => { const snapshot = structuredClone(args); return datasetWrite("Create dataset export", snapshot[0], () => transportCreateDatasetExport(...snapshot), [], undefined, "job") };
export const createTrainingRun = (...args: Parameters<typeof transportCreateTrainingRun>): ReturnType<typeof transportCreateTrainingRun> => { const snapshot = structuredClone(args); return datasetWrite("Create training run", snapshot[0], () => transportCreateTrainingRun(...snapshot), [], undefined, "job") };
export const cancelTrainingRun = (...args: Parameters<typeof transportCancelTrainingRun>): ReturnType<typeof transportCancelTrainingRun> => { const snapshot = structuredClone(args); return datasetWrite("Cancel training run", undefined, () => transportCancelTrainingRun(...snapshot), [], undefined, "job") };
export const rescoreTrainingRun = (...args: Parameters<typeof transportRescoreTrainingRun>): ReturnType<typeof transportRescoreTrainingRun> => { const snapshot = structuredClone(args); return datasetWrite("Rescore training run", undefined, () => transportRescoreTrainingRun(...snapshot), [], undefined, "job") };
export const createEvalBatch = (...args: Parameters<typeof transportCreateEvalBatch>): ReturnType<typeof transportCreateEvalBatch> => { const snapshot = structuredClone(args); return datasetWrite("Create eval batch", undefined, () => transportCreateEvalBatch(...snapshot), [], undefined, "job") };
export const cancelEvalBatch = (...args: Parameters<typeof transportCancelEvalBatch>): ReturnType<typeof transportCancelEvalBatch> => { const snapshot = structuredClone(args); return datasetWrite("Cancel eval batch", undefined, () => transportCancelEvalBatch(...snapshot), [], undefined, "job") };
export const createDatasetFromPerson = (...args: Parameters<typeof transportCreateDatasetFromPerson>): ReturnType<typeof transportCreateDatasetFromPerson> => { const snapshot = structuredClone(args); return datasetWrite("Create dataset from person", undefined, () => transportCreateDatasetFromPerson(...snapshot), []) };
export const dedupeDataset = (...args: Parameters<typeof transportDedupeDataset>): ReturnType<typeof transportDedupeDataset> => { const snapshot = structuredClone(args); return datasetWrite("Dedupe dataset", snapshot[0], () => transportDedupeDataset(...snapshot) .catch(error => { if (isDefinitiveMutationError(error)) throw new Error(error.message); throw error; }), []) };
export const reincludeDatasetItems = (...args: Parameters<typeof transportReincludeDatasetItems>): ReturnType<typeof transportReincludeDatasetItems> => { const snapshot = structuredClone(args); return datasetWrite("Reinclude dataset items", snapshot[0], () => transportReincludeDatasetItems(...snapshot) .catch(error => { if (isDefinitiveMutationError(error)) throw new Error(error.message); throw error; }), []) };
export const fillDatasetGaps = (...args: Parameters<typeof transportFillDatasetGaps>): ReturnType<typeof transportFillDatasetGaps> => { const snapshot = structuredClone(args); return datasetWrite("Fill dataset gaps", snapshot[0], () => transportFillDatasetGaps(...snapshot) .catch(error => { if (isDefinitiveMutationError(error)) throw new Error(error.message); throw error; }), []) };
export const backfillDatasetPose = (...args: Parameters<typeof transportBackfillDatasetPose>): ReturnType<typeof transportBackfillDatasetPose> => { const snapshot = structuredClone(args); return datasetWrite("Backfill dataset pose", snapshot[0], () => transportBackfillDatasetPose(...snapshot), [], undefined, "job") };
export const mineDatasetFrames = (...args: Parameters<typeof transportMineDatasetFrames>): ReturnType<typeof transportMineDatasetFrames> => { const snapshot = structuredClone(args); return datasetWrite("Mine dataset frames", snapshot[0], () => transportMineDatasetFrames(...snapshot), [], undefined, "job") };
export const autoSelectDataset = (...args: Parameters<typeof transportAutoSelectDataset>): ReturnType<typeof transportAutoSelectDataset> => { const snapshot = structuredClone(args); return datasetWrite("Auto select dataset", snapshot[0], () => transportAutoSelectDataset(...snapshot) .catch(error => { if (isDefinitiveMutationError(error)) throw new Error(error.message); throw error; }), []) };
export const buildRegularizationDataset = (...args: Parameters<typeof transportBuildRegularizationDataset>): ReturnType<typeof transportBuildRegularizationDataset> => { const snapshot = structuredClone(args); return datasetWrite("Build regularization dataset", snapshot[0], () => transportBuildRegularizationDataset(...snapshot), []) };
export const batchCropDatasetItems = (...args: Parameters<typeof transportBatchCropDatasetItems>): ReturnType<typeof transportBatchCropDatasetItems> => { const snapshot = structuredClone(args); return datasetWrite("Batch crop dataset items", snapshot[0], () => transportBatchCropDatasetItems(...snapshot) .catch(error => { if (isDefinitiveMutationError(error)) throw new Error(error.message); throw error; }), []) };
export const updateDatasetCaption = (...args: Parameters<typeof transportUpdateDatasetCaption>): ReturnType<typeof transportUpdateDatasetCaption> => { const snapshot = structuredClone(args); return datasetWrite("Update dataset caption", snapshot[0], () => transportUpdateDatasetCaption(...snapshot), [{ kind: "fields", key: `dataset:${snapshot[0]}/item/${snapshot[1]}`, values: { caption_override: snapshot[2], caption: snapshot[2], effective_caption: snapshot[2], source: "override" } }]) };
export const markDatasetCaptionReviewed = (...args: Parameters<typeof transportMarkDatasetCaptionReviewed>): ReturnType<typeof transportMarkDatasetCaptionReviewed> => { const snapshot = structuredClone(args); return datasetWrite("Mark dataset caption reviewed", snapshot[0], () => transportMarkDatasetCaptionReviewed(...snapshot), []) };
export const generateDatasetCaptions = (...args: Parameters<typeof transportGenerateDatasetCaptions>): ReturnType<typeof transportGenerateDatasetCaptions> => { const snapshot = structuredClone(args); return datasetWrite("Generate dataset captions", snapshot[0], () => transportGenerateDatasetCaptions(...snapshot), [], undefined, "job") };
