export interface CurationStatus {
  enabled: boolean;
  mode: "disabled" | "fixture" | "production";
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
  review: {
    id: string;
    decision: CurationDecision;
    actor_kind: string;
    human_presence_verified?: boolean;
    presence_evidence?: {
      method?: string;
      user_present?: boolean;
      user_verified?: boolean;
    };
  } | null;
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
  actor: {
    id: string;
    kind: "human" | "agent" | "fixture_human";
    human_presence_verified: boolean;
    operations?: string[];
    enrolled?: boolean;
  };
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

export interface CurationAuthStatus {
  enrolled: boolean;
  enrollment_available: boolean;
  actor_kind: "human" | "agent" | "fixture_human";
}

type CredentialDescriptorJSON = Omit<PublicKeyCredentialDescriptor, "id"> & { id: string };
export type CurationRegistrationOptions = Omit<PublicKeyCredentialCreationOptions, "challenge" | "user" | "excludeCredentials"> & {
  challenge: string;
  user: Omit<PublicKeyCredentialUserEntity, "id"> & { id: string };
  excludeCredentials?: CredentialDescriptorJSON[];
};
export type CurationAssertionOptions = Omit<PublicKeyCredentialRequestOptions, "challenge" | "allowCredentials"> & {
  challenge: string;
  allowCredentials?: CredentialDescriptorJSON[];
};
export interface CurationChallenge<T> {
  challenge_id: string;
  public_key: T;
}
export interface CurationPasskeyCredential {
  id: string;
  rawId: string;
  type: "public-key";
  authenticatorAttachment?: string;
  clientExtensionResults: unknown;
  response: {
    clientDataJSON: string;
    attestationObject?: string;
    transports?: string[];
    authenticatorData?: string;
    signature?: string;
    userHandle?: string | null;
  };
}
export interface CurationPresence {
  challenge_id: string;
  credential: CurationPasskeyCredential;
}
