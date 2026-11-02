// Offline, reproducible domain map from the installed CC0 simple-icons metadata.
import { readFileSync, writeFileSync } from 'node:fs';
const icons = JSON.parse(readFileSync(new URL('../frontend/node_modules/simple-icons/data/simple-icons.json', import.meta.url)));
const ignored = new Set(['github.com', 'gitlab.com', 'wikipedia.org', 'wikimedia.org', 'figma.com', 'w3.org', 'archive.org', 'behance.net', 'dribbble.com', 'google.com']);
const mapping = {};
const ambiguous = new Set();
for (const icon of icons) {
  for (const source of [icon.source, icon.guidelines, ...(Object.values(icon.aliases?.loc ?? {}).flat().map(alias => alias.source) ?? [])]) {
    if (!source) continue;
    let host;
    try { host = new URL(source).hostname.replace(/^www\./, ''); } catch { continue; }
    const parts = host.split('.');
    const suffix = parts.slice(-2).join('.');
    const domain = /^(co|com|org|net)\.[a-z]{2}$/.test(suffix) ? parts.slice(-3).join('.') : suffix;
    if (ignored.has(domain) || parts.length < 2) continue;
    if (mapping[domain] && mapping[domain].simple_icons_slug !== icon.slug) { ambiguous.add(domain); continue; }
    mapping[domain] = { display_name: icon.title, simple_icons_slug: icon.slug, colour: icon.hex };
  }
}
for (const domain of ambiguous) delete mapping[domain];
const sorted = Object.fromEntries(Object.entries(mapping).sort(([a], [b]) => a.localeCompare(b)));
writeFileSync(new URL('../app/services/social_icon_domains.json', import.meta.url), `${JSON.stringify(sorted, null, 2)}\n`);
console.log(`Generated ${Object.keys(sorted).length} domain mappings from installed simple-icons metadata (no network).`);
