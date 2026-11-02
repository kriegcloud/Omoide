/** Stable, sorted query identity; snapshot filters at confirmation time. */
export type EntityKind = "watermark" | "media" | "face" | "person" | "duplicate" | "album" | "tag" | "config";
export type EntityKey = `${EntityKind}:${string | number}`;
export type QueryKey = string;
export function queryKey(scope: string, filters: Readonly<Record<string, string | number | boolean | null | undefined>> = {}): QueryKey {
  const params = new URLSearchParams();
  for (const key of Object.keys(filters).sort()) {
    const value = filters[key];
    if (value !== undefined && value !== null) params.set(key, String(value));
  }
  return params.size ? `${scope}?${params}` : scope;
}
export const entityKey = (kind: EntityKind, id: string | number): EntityKey => `${kind}:${id}`;
/** Explicit kinds override this compatibility mapping for legacy cache names. */
export function queryEntityKind(key: QueryKey): EntityKind | undefined {
  if (/^watermarks/.test(key)) return "watermark";
  if (/duplicate/.test(key)) return "duplicate";
  if (/^(orphan|face)/.test(key) || key.includes('/faces')) return "face";
  if (/^people/.test(key) || /\/persons?(?:\?|$)/.test(key)) return "person";
  if (/^tags?(?:-|:|$)/.test(key) || /\/tags?(?:\?|$)/.test(key)) return "tag";
  if (/\/search\/scene(?:\?|$)/.test(key) || key.endsWith('-timeline')) return undefined;
  return "media";
}
