import { API } from "../config";
import type {
  DatasetExport,
  DatasetExportLayout,
  DatasetItem,
  DatasetItemPage,
  TrainingDataset,
} from "../types";
import type { EditOp, FilerobotDesignState } from "../utils/editorOps";

type DatasetInput = Partial<Omit<TrainingDataset, "id" | "created_at" | "updated_at" | "item_count" | "included_count">> & { name: string };

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? `Request failed (${response.status})`);
  }
  return response.json();
}

export const getDatasets = () => fetch(`${API}/api/datasets`).then(json<TrainingDataset[]>);
export const getDataset = (id: number) => fetch(`${API}/api/datasets/${id}`).then(json<TrainingDataset>);
export const createDataset = (input: DatasetInput) => fetch(`${API}/api/datasets`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
}).then(json<TrainingDataset>);
export const updateDataset = (id: number, input: Partial<TrainingDataset>) => fetch(`${API}/api/datasets/${id}`, {
  method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
}).then(json<TrainingDataset>);
export const deleteDataset = (id: number) => fetch(`${API}/api/datasets/${id}`, { method: "DELETE" });
export const addDatasetItems = (id: number, mediaIds: number[]) => fetch(`${API}/api/datasets/${id}/items`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ media_ids: mediaIds }),
}).then(json<{ added_ids: number[]; skipped_ids: number[] }>);
export const removeDatasetItems = (id: number, mediaIds: number[]) => fetch(`${API}/api/datasets/${id}/items`, {
  method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ media_ids: mediaIds }),
}).then(json<{ added_ids: number[]; skipped_ids: number[] }>);
export const getDatasetItems = (id: number, cursor?: string | null) => {
  const params = new URLSearchParams({ limit: "500" });
  if (cursor) params.set("cursor", cursor);
  return fetch(`${API}/api/datasets/${id}/items?${params}`).then(json<DatasetItemPage>);
};
export const updateDatasetItem = (datasetId: number, itemId: number, input: {
  caption_override?: string | null; edit_ops?: EditOp[] | null; edit_design_state?: FilerobotDesignState | null;
  excluded?: boolean; weight?: number; position?: number;
}) => fetch(`${API}/api/datasets/${datasetId}/items/${itemId}`, {
  method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
}).then(json<DatasetItem>);
export const createDatasetExport = (id: number, layout?: DatasetExportLayout) => fetch(`${API}/api/datasets/${id}/export`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ layout }),
}).then(json<DatasetExport>);
export const getDatasetExports = (id: number) => fetch(`${API}/api/datasets/${id}/exports`).then(json<DatasetExport[]>);
export const createDatasetFromPerson = (personId: number) => fetch(`${API}/api/datasets/from-person/${personId}`, { method: "POST" }).then(json<TrainingDataset>);
export const datasetManifestUrl = (exportId: number) => `${API}/api/datasets/exports/${exportId}/manifest`;
