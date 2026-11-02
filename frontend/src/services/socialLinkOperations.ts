import { startOperation, type OptimisticCommand } from '../stores/optimisticOperations';
import type { SocialLink } from '../types';
export const socialLinksQuery = (personId: number) => `person-social:${personId}`;
/** One person resource serializes list edits with add/delete/reorder and other person writes. */
export function startSocialLinkOperation<R>(options: {
  personId: number; label: string; links: SocialLink[]; request: () => Promise<R>;
  lookup?: (id: string) => Promise<unknown>; refresh?: () => Promise<void>;
}) {
  const key = `person:${options.personId}` as const;
  const command: OptimisticCommand<Record<string, never>, R> = {
    label: options.label, input: {}, resources: [key],
    patches: [{ kind: 'fields', key, query: socialLinksQuery(options.personId), values: { social_links: options.links } }],
    request: options.request, lookup: options.lookup, refresh: options.refresh,
  };
  return startOperation(command);
}
