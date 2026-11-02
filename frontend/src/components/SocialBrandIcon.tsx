import { useEffect, useState } from 'react';
import { Box, SvgIcon } from '@mui/material';
import LinkIcon from '@mui/icons-material/Link';
import { API } from '../config';
import type { SocialLinkPreview } from '../types';
interface BrandVector { slug: string; path: string; hex?: string }
// One lazily loaded module per brand SVG: a person page fetches only the icons
// it shows (a few hundred bytes each) instead of the whole 5 MB catalogue.
const vectors = import.meta.glob<string>('../../node_modules/simple-icons/icons/*.svg', { query: '?raw', import: 'default' });
const loaded = new Map<string, Promise<BrandVector | null>>();
function loadVector(slug: string): Promise<BrandVector | null> {
  const key = `../../node_modules/simple-icons/icons/${slug}.svg`;
  let pending = loaded.get(slug);
  if (!pending) {
    const load = vectors[key];
    pending = load ? load().then(svg => {
      const path = /<path[^>]*\sd="([^"]+)"/.exec(svg)?.[1];
      return path ? { slug, path } : null;
    }) : Promise.resolve(null);
    loaded.set(slug, pending);
  }
  return pending;
}
export function SocialBrandIcon({ link }: { link: Pick<SocialLinkPreview, 'platform' | 'brand'> & { brand_domain?: string } }) {
  const [failedUrl, setFailedUrl] = useState('');
  const [resolved, setResolved] = useState<BrandVector | null>(null);
  const slug = link.brand?.simple_icons_slug || link.platform;
  const manual = link.brand?.source === 'manual';
  const icon = manual ? null : (resolved?.slug === slug ? resolved : null);
  useEffect(() => {
    if (manual || !link.brand?.simple_icons_slug) return;
    let active = true;
    void loadVector(slug).then(vector => { if (active) setResolved(vector); }).catch(() => { /* Cached site icon or generic link remains available. */ });
    return () => { active = false; };
  }, [slug, manual, link.brand?.simple_icons_slug]);
  const colour = link.brand?.colour || icon?.hex;
  const url = link.brand?.icon_path && link.brand_domain ? `${API}/api/social-brands/${encodeURIComponent(link.brand_domain)}/icon?v=${encodeURIComponent(link.brand.fetched_at || '')}` : '';
  return <Box component="span" sx={{ display: 'inline-flex', color: 'text.secondary', '.social-link-row:hover &': { color: colour ? `#${colour.replace(/^#/, '')}` : 'text.primary' }, flexShrink: 0 }}>
    {icon ? <SvgIcon fontSize="small" aria-hidden><path d={icon.path} fill="currentColor" /></SvgIcon> : url && url !== failedUrl ? <Box component="img" src={url} alt="" onError={() => setFailedUrl(url)} sx={{ width: 20, height: 20, objectFit: 'contain' }} /> : <LinkIcon fontSize="small" />}
  </Box>;
}
