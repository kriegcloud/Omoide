import type { SocialLink } from '../types';
export function groupSocialLinks(links: readonly SocialLink[]) {
  const groups = new Map<string, { key: string; name: string; links: SocialLink[] }>();
  for (const link of [...links].sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0))) {
    const key = link.platform === 'other' ? link.brand_domain || 'other' : link.platform;
    const name = link.brand?.display_name || ({ x: 'X', tiktok: 'TikTok', youtube: 'YouTube', onlyfans: 'OnlyFans' }[key] ?? (key === 'other' ? 'Website' : key[0].toUpperCase() + key.slice(1)));
    const group = groups.get(key) ?? { key, name, links: [] };
    group.links.push(link); groups.set(key, group);
  }
  return [...groups.values()];
}
