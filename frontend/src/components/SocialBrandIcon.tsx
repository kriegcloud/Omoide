import { useEffect, useState } from 'react';
import { Box, SvgIcon } from '@mui/material';
import LinkIcon from '@mui/icons-material/Link';
import type { SimpleIcon } from 'simple-icons';
import { API } from '../config';
import type { SocialLinkPreview } from '../types';
let catalogue: Promise<Record<string, SimpleIcon>> | undefined;
/** Keep the vector catalogue outside the initial application module graph. */
function loadCatalogue() {
  return catalogue ??= import('simple-icons').then(module => Object.fromEntries(Object.values(module).filter((value): value is SimpleIcon => !!value && typeof value === 'object' && 'slug' in value && 'path' in value).map(icon => [icon.slug, icon])));
}
export function SocialBrandIcon({ link }: { link: Pick<SocialLinkPreview, 'platform' | 'brand'> & { brand_domain?: string } }) {
  const [failedUrl, setFailedUrl] = useState('');
  const [resolved, setResolved] = useState<SimpleIcon | null>(null);
  const slug = link.brand?.simple_icons_slug || link.platform;
  const manual = link.brand?.source === 'manual';
  const icon = manual ? null : (resolved?.slug === slug ? resolved : null);
  useEffect(() => {
    if (manual || !link.brand?.simple_icons_slug) return;
    let active = true;
    void loadCatalogue().then(all => { if (active) setResolved(all[slug] || null); }).catch(() => { /* Cached site icon or generic link remains available. */ });
    return () => { active = false; };
  }, [slug, manual, link.brand?.simple_icons_slug]);
  const colour = link.brand?.colour || icon?.hex;
  const url = link.brand?.icon_path && link.brand_domain ? `${API}/api/social-brands/${encodeURIComponent(link.brand_domain)}/icon?v=${encodeURIComponent(link.brand.fetched_at || '')}` : '';
  return <Box component="span" sx={{ display: 'inline-flex', color: 'text.secondary', '.social-link-row:hover &': { color: colour ? `#${colour.replace(/^#/, '')}` : 'text.primary' }, flexShrink: 0 }}>
    {icon ? <SvgIcon fontSize="small" aria-hidden><path d={icon.path} fill="currentColor" /></SvgIcon> : url && url !== failedUrl ? <Box component="img" src={url} alt="" onError={() => setFailedUrl(url)} sx={{ width: 20, height: 20, objectFit: 'contain' }} /> : <LinkIcon fontSize="small" />}
  </Box>;
}
