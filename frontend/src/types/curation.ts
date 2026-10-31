export interface CurationStatus {
  enabled: boolean;
  fixture_only: boolean;
  generative_enabled: boolean;
  human_presence_verified: boolean;
}

export interface CurationDatasetSummary {
  id: string;
  name: string;
  revision: number;
}

export interface CurationSource {
  id: string;
  label: string;
  sha256: string;
  status: string;
}

export interface CurationCaption {
  id: string;
  text: string;
  sha256: string;
}

export type CurationDecision = "accept" | "reject" | "defer";

export interface CurationItem {
  artifact_id: string;
  source_id: string;
  sha256: string;
  width: number;
  height: number;
  generative_ancestry: boolean;
  caption: CurationCaption | null;
  review: { id: string; decision: CurationDecision; actor_kind: string } | null;
  eligible: boolean;
  blockers: string[];
  transform_summary?: string;
  uncertainties?: string[];
}

export interface CurationExportReceipt {
  id: string;
  dataset_id: string;
  status: "admitted" | "running" | "succeeded" | "blocked";
  snapshot_revision: number;
  manifest_sha256: string | null;
  item_count: number;
  error_code: string | null;
}

export interface CurationDataset extends CurationDatasetSummary {
  policy_version: string;
  actor: { id: string; kind: "agent" | "fixture_human"; human_presence_verified: boolean };
  sources: CurationSource[];
  items: CurationItem[];
  exports: CurationExportReceipt[];
  remaining_count: number;
  blockers: string[];
}

export interface CurationReviewInput {
  artifact_id: string;
  caption_id: string;
  asset_sha256: string;
  caption_sha256: string;
  decision: CurationDecision;
  rationale: string;
  expected_revision: number;
}
