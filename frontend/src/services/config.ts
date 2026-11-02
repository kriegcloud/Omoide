import { DefinitiveMutationError, startOperation } from "../stores/optimisticOperations";
import { API } from "../config";
import { AppConfig, ProfileListResponse, ProfileHealth } from "../types";

// Share a single in-flight/settled promise so concurrent and repeat callers
// (e.g. per-navigation detail pages) reuse one request.
let configPromise: Promise<AppConfig> | null = null;

export const getConfig = (): Promise<AppConfig> => {
  if (!configPromise) {
    configPromise = (async () => {
      const response = await fetch(`${API}/api/config/`);
      if (!response.ok) {
        throw new Error("Failed to fetch config");
      }
      return response.json();
    })().catch((error) => {
      // Do not cache failures; allow the next caller to retry.
      configPromise = null;
      throw error;
    });
  }
  return configPromise;
};

const invalidateConfigCache = () => {
  configPromise = null;
};

export const saveConfig = async (
  config: AppConfig,
  acknowledgeMediaDirRemovals = false
): Promise<AppConfig> => {
  const response = await fetch(`${API}/api/config/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      settings: config,
      acknowledge_media_dir_removals: acknowledgeMediaDirRemovals,
    }),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    const message = err?.detail?.message || (typeof err?.detail === "string" ? err.detail : "Failed to save config");
    // These guards reject before entering the save/purge handler. Other statuses
    // can follow partial catalog deletion and must remain uncertain.
    if ([403, 422].includes(response.status) || err?.detail?.requires_acknowledgement === true) {
      throw new DefinitiveMutationError(message);
    }
    throw new Error(message);
  }
  invalidateConfigCache();
  return response.json();
};

export const reloadConfig = async (
  acceptedConfig?: AppConfig
): Promise<AppConfig> => {
  const response = await fetch(`${API}/api/config/reload`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error("Failed to reload config");
  }

  // Presentation mode disables config reads as soon as reload succeeds.
  // The save response is already the accepted server configuration.
  invalidateConfigCache();
  const latest = acceptedConfig?.general.presentation_mode
    ? acceptedConfig
    : await getConfig();
  configPromise = Promise.resolve(latest);

  // In production builds, config getters read from window.runtimeConfig.
  // Update these values so components referencing config.PRESENTATION_MODE (etc.)
  // see the new values without a full page refresh.
  if (!import.meta.env.DEV) {
    const ro = String(!!latest.general.presentation_mode);
    const people = String(!!latest.general.enable_people);
    const meme = String(!!latest.general.meme_mode);
    window.runtimeConfig = {
      VITE_API_IS_DOCKER: window.runtimeConfig?.VITE_API_IS_DOCKER ?? "false",
      APP_VERSION: window.runtimeConfig?.APP_VERSION ?? "",
      ...(window.runtimeConfig ?? {}),
      VITE_API_PRESENTATION_MODE: ro,
      VITE_API_ENABLE_PEOPLE: people,
      VITE_API_MEME_MODE: meme,
      VIDEO_CONVERT_ON_THE_FLY: String(!!latest.video?.convert_on_the_fly),
      VITE_API_REPAIRS_ENABLED: String(!!latest.repairs.enabled),
      PERSON_RELATIONSHIP_MAX_NODES: String(
        latest.general.person_relationship_max_nodes ?? 100
      ),
    };

    // Emit a lightweight event that components can listen to if they need
    // to react to config changes proactively (e.g., force a re-render).
    try {
      window.dispatchEvent(new Event("runtime-config-updated"));
    } catch {
      // no-op: dispatchEvent can fail in some test environments
    }
  }

  return latest;
};

export const pickDirectory = async (): Promise<string | null> => {
  const response = await fetch(`${API}/api/config/pick-directory`);
  if (!response.ok) {
    // Return null so the caller can fall back to manual entry
    return null;
  }
  const data = await response.json();
  return (data?.path as string) || null;
};

export const listProfiles = async (): Promise<ProfileListResponse> => {
  const res = await fetch(`${API}/api/config/profiles`);
  if (!res.ok) {
    throw new Error("Failed to list profiles");
  }
  return res.json();
};

export const switchProfile = async (path: string): Promise<void> => {
  const res = await fetch(`${API}/api/config/profiles/switch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to switch profile");
  }
};

export const createProfile = async (
  path: string,
  name: string
): Promise<void> => {
  const res = await fetch(`${API}/api/config/profiles/create`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, name }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to create profile");
  }
};

export const removeProfile = async (path: string): Promise<void> => {
  const res = await fetch(`${API}/api/config/profiles/remove`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to remove profile");
  }
};

export const moveData = async (destPath: string): Promise<void> => {
  const res = await fetch(`${API}/api/config/move-data`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dest_path: destPath }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to move data directory");
  }
};

export const getProfileHealth = async (): Promise<ProfileHealth> => {
  const res = await fetch(`${API}/api/config/profile-health`);
  if (!res.ok) throw new Error("Failed to fetch profile health");
  return res.json();
};

export const addExistingProfile = async (
  path: string,
  name?: string
): Promise<void> => {
  const res = await fetch(`${API}/api/config/profiles/add`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, name }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to add profile");
  }
};

export type ScanFolderStatus = "scanned" | "partial" | "outside" | "excluded";

export interface ScanFolderEntry {
  name: string;
  path: string;
  has_subfolders: boolean;
  status: ScanFolderStatus;
}

export interface ScanFolderListing {
  parent: string;
  folders: ScanFolderEntry[];
}

export interface ScanScopeDraft {
  include_folders: string[];
  exclude_folders: string[];
  exclude_folder_patterns: string[];
}

/**
 * Lists real subfolders of `parent` under the media directories, labelled with
 * how a scan would treat each one under the given (unsaved) scope.
 */
export const listScanFolders = async (
  parent: string,
  scope: ScanScopeDraft,
  signal?: AbortSignal
): Promise<ScanFolderListing> => {
  const params = new URLSearchParams({ parent });
  scope.include_folders.forEach((v) => params.append("include", v));
  scope.exclude_folders.forEach((v) => params.append("exclude", v));
  scope.exclude_folder_patterns.forEach((v) => params.append("pattern", v));
  const response = await fetch(`${API}/api/config/scan-folders?${params}`, {
    signal,
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err?.detail || "Failed to list folders");
  }
  return response.json();
};

export interface ScanFolderCrumb {
  name: string;
  path: string;
  status: ScanFolderStatus;
}

export interface ScanFolderMatch extends ScanFolderEntry {
  /** Ancestors from the top-level folder down to the parent of the match. */
  trail: ScanFolderCrumb[];
}

export interface ScanFolderSearchResult {
  query: string;
  folders: ScanFolderMatch[];
  truncated: boolean;
  /** False while the server's folder index builds; results come from a time-boxed walk. */
  indexed: boolean;
}

/**
 * Finds folders anywhere under the media directories whose name (or relative
 * path, when the query contains "/") matches `query`, labelled against the
 * given (unsaved) scope. Queries under two characters return nothing.
 */
export const searchScanFolders = async (
  query: string,
  scope: ScanScopeDraft,
  signal?: AbortSignal,
  limit = 25
): Promise<ScanFolderSearchResult> => {
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  scope.include_folders.forEach((v) => params.append("include", v));
  scope.exclude_folders.forEach((v) => params.append("exclude", v));
  scope.exclude_folder_patterns.forEach((v) => params.append("pattern", v));
  const response = await fetch(
    `${API}/api/config/scan-folders/search?${params}`,
    { signal }
  );
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err?.detail || "Failed to search folders");
  }
  return response.json();
};

/** In-memory only: configuration may contain secrets; never persist drafts to storage. */
interface ConfigDraftState {
  draft: AppConfig | null;
  accepted: AppConfig | null;
  applied: AppConfig | null;
  revision: number;
  phase: "idle" | "saving" | "applying" | "applied" | "profile-switched" | "save-failed" | "apply-failed";
  error: string | null;
}
let draftState: ConfigDraftState = {
  draft: null, accepted: null, applied: null, revision: 0, phase: "idle", error: null,
};
const draftListeners = new Set<() => void>();
export const getConfigDraftState = () => draftState;
export const subscribeConfigDraft = (listener: () => void) => {
  draftListeners.add(listener);
  return () => { draftListeners.delete(listener); };
};
function publishDraft(update: Partial<ConfigDraftState>) {
  draftState = { ...draftState, ...update };
  draftListeners.forEach(listener => listener());
}
export function initializeConfigDraft(config: AppConfig) {
  if (!draftState.draft) publishDraft({ draft: config, accepted: config, applied: config });
}
export function updateConfigDraft(update: AppConfig | null | ((previous: AppConfig | null) => AppConfig | null)) {
  const draft = typeof update === "function" ? update(draftState.draft) : update;
  if (draft !== draftState.draft) publishDraft({ draft, revision: draftState.revision + 1 });
}
/** Snapshot intent once; the request and global feedback survive route unmount. */
export function submitConfigDraft(config: AppConfig, acknowledgeRemovals = false, targetProfile: string | null = null): boolean {
  // Failed transports have no durable config receipt. Never replay an uncertain
  // save (directory removals can purge catalog records) within this session.
  if (!["idle", "applied"].includes(draftState.phase)) return false;
  const revision = draftState.revision;
  publishDraft({ phase: "saving", error: null });
  try {
    startOperation({
      label: "Save and apply configuration",
      input: { config, acknowledgeRemovals, targetProfile },
      resources: ["config:active"], patches: [],
      request: async (input) => {
        let accepted: AppConfig | null = null;
        try {
          accepted = await saveConfig(input.config, input.acknowledgeRemovals);
          publishDraft({ accepted, phase: "applying" });
          if (input.targetProfile) {
            // Switching activates the target settings itself. Applying the old
            // profile first could enable presentation mode and forbid switching.
            await switchProfile(input.targetProfile);
            publishDraft({ phase: "profile-switched" });
            return accepted;
          }
          const latest = await reloadConfig(accepted);
          publishDraft({
            applied: latest, phase: "applied",
            // Only normalize the submitted version; never replace newer edits.
            ...(draftState.revision === revision ? { draft: latest } : {}),
          });
          return latest;
        } catch (error) {
          const detail = error instanceof Error ? error.message : String(error);
          const rejected = !accepted && error instanceof DefinitiveMutationError;
          publishDraft({ phase: rejected ? "idle" : accepted ? "apply-failed" : "save-failed", error:
            rejected ? detail : `${accepted ? (input.targetProfile ? "Saved to disk; profile activation was not confirmed." : "Saved to disk; runtime apply was not confirmed.") : "Save outcome was not confirmed."} ${detail} Draft retained in this tab. Verify server state before retrying.` });
          if (rejected) throw error;
          throw new Error(draftState.error!);
        }
      },
    });
  } catch (error) {
    publishDraft({ phase: "idle", error: error instanceof Error ? error.message : String(error) });
    return false;
  }
  return true;
}
