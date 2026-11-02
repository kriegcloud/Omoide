import type { CropFraming } from "../types";

export const editorTools = {
  crop: ["Adjust", "Crop"], rotate: ["Adjust", "Rotate"],
  "flip-x": ["Adjust", "Flip_X"], "flip-y": ["Adjust", "Flip_Y"],
  brightness: ["Finetune", "Brightness"], contrast: ["Finetune", "Contrast"],
  hsv: ["Finetune", "HueSaturationValue"], warmth: ["Finetune", "Warmth"],
  blur: ["Finetune", "Blur"], threshold: ["Finetune", "Threshold"],
  posterize: ["Finetune", "Posterize"], pixelate: ["Finetune", "Pixelate"],
  noise: ["Finetune", "Noise"], resize: ["Resize", "Resize"],
} as const;
export type EditorTool = keyof typeof editorTools;
export type EditorQueryKey = "edit" | "crop" | "tool" | "guides" | "preset" | "compare" | "upscaler" | "ratio" | "adv";
export type EditorQueryPatch = Partial<Record<EditorQueryKey, string | null>>;
const optionKeys = ["tool", "guides", "preset", "compare", "upscaler", "ratio", "adv"] as const;

export function positiveId(value: string | null): number | null {
  if (!value || !/^[1-9]\d*$/.test(value)) return null;
  const id = Number(value);
  return Number.isSafeInteger(id) ? id : null;
}
export function readEditorOptions(params: URLSearchParams) {
  const key = params.get("tool") ?? "crop";
  const [tabId, toolId] = editorTools[key as EditorTool] ?? editorTools.crop;
  const guides = (params.get("guides") ?? "").split(",");
  const preset = params.get("preset");
  return {
    upscale: key === "upscale", upscaler: params.get("upscaler"),
    ratio: params.get("ratio") ?? "free", advanced: params.get("adv") === "1",
    tabId, toolId, face: guides.includes("face"), bucket: guides.includes("bucket"),
    compare: params.get("compare") === "1",
    preset: (["closeup", "portrait", "half_body", "full_body"].includes(preset ?? "") ? preset : null) as CropFraming | null,
  };
}
export function editorSelection(tabId: string, toolId: string | null): EditorTool {
  return (Object.entries(editorTools).find(([, [tab, tool]]) => tab === tabId && (tab === "Resize" || tool === toolId))?.[0] ?? "crop") as EditorTool;
}
export function patchEditorSearch(current: URLSearchParams, patch: EditorQueryPatch) {
  const next = new URLSearchParams(current);
  if (patch.edit != null) next.delete("crop");
  if (patch.crop != null) next.delete("edit");
  for (const [key, value] of Object.entries(patch)) {
    if (value === null) next.delete(key); else next.set(key, value);
  }
  if (patch.edit === null || patch.crop === null) for (const key of optionKeys) next.delete(key);
  return next;
}

/**
 * How a media menu opens the editor. Grid pages use the global editor
 * (`?edit=<id>`); the media viewer owns its editor and only accepts `edit=1`,
 * stripping any other value, so a menu inside the viewer must use that form
 * (or open the other item's viewer when the menu belongs to a different media).
 */
export type EditorOpenTarget =
  | { kind: "patch"; edit: string }
  | { kind: "navigate"; to: string };

export function editorOpenTarget(pathname: string, mediaId: number): EditorOpenTarget {
  const viewer = /^\/medium\/(\d+)\/?$/.exec(pathname);
  if (!viewer) return { kind: "patch", edit: String(mediaId) };
  if (Number(viewer[1]) === mediaId) return { kind: "patch", edit: "1" };
  return { kind: "navigate", to: `/medium/${mediaId}?edit=1` };
}

/**
 * Opening the editor pushes one history entry and marks it. Closing pops that
 * entry when we pushed it, otherwise replaces the URL. Pushing on close left
 * `viewer → viewer?edit=1 → viewer` in history, so the viewer's own Back-based
 * close landed on `?edit=1` and reopened the editor in a loop.
 */
export const EDITOR_PUSHED = "editorPushed";

export function markEditorPushed(state: unknown): Record<string, unknown> {
  return { ...(state && typeof state === "object" ? state as Record<string, unknown> : {}), [EDITOR_PUSHED]: true };
}

export function editorCloseAction(state: unknown): "back" | "replace" {
  return state && typeof state === "object" && (state as Record<string, unknown>)[EDITOR_PUSHED] === true ? "back" : "replace";
}
