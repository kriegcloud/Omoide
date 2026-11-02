from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from app.schemas.person import SocialLinkSuggestion, SocialPlatform

HANDLE_PATTERN = re.compile(r"^[A-Za-z0-9._]{2,30}$")
GENERIC_FOLDERS = {"stories", "posts", "camera", "dcim", "downloads"}

PLATFORM_WORDS: tuple[tuple[str, SocialPlatform], ...] = (
    ("instagram", "instagram"),
    ("tiktok", "tiktok"),
    ("twitter", "x"),
    ("youtube", "youtube"),
    ("onlyfans", "onlyfans"),
    ("threads", "threads"),
    ("facebook", "facebook"),
    ("snapchat", "snapchat"),
)


def normalize_handle(platform: SocialPlatform, raw: str) -> str:
    value = raw.strip().rstrip("/").strip()
    if platform == "x":
        candidate = value if "://" in value else f"https://{value}"
        parsed = urlparse(candidate)
        hostname = (parsed.hostname or "").lower().removeprefix("www.")
        if hostname in {"x.com", "twitter.com"}:
            value = parsed.path.strip("/").split("/", maxsplit=1)[0]
    return value.lstrip("@").strip()


def derive_url(platform: SocialPlatform, handle: str) -> str:
    normalized = normalize_handle(platform, handle)
    if platform == "other":
        raise ValueError("Platform 'other' requires an explicit URL")
    templates = {
        "instagram": "https://instagram.com/{handle}",
        "tiktok": "https://tiktok.com/@{handle}",
        "x": "https://x.com/{handle}",
        "youtube": "https://youtube.com/@{handle}",
        "onlyfans": "https://onlyfans.com/{handle}",
        "threads": "https://threads.net/@{handle}",
        "facebook": "https://facebook.com/{handle}",
        "snapchat": "https://snapchat.com/add/{handle}",
    }
    return templates[platform].format(handle=normalized)


def _platform_from_ancestors(path: Path) -> SocialPlatform | None:
    for folder in reversed(path.parent.parent.parts):
        folded = folder.casefold()
        for word, platform in PLATFORM_WORDS:
            if word in folded:
                return platform
        if folded == "x":
            return "x"
    return None


def suggest_from_paths(paths: Iterable[str]) -> list[SocialLinkSuggestion]:
    counts: dict[str, int] = {}
    platforms: dict[str, set[SocialPlatform]] = {}
    for raw_path in paths:
        path = Path(raw_path)
        handle = path.parent.name
        folded = handle.casefold()
        if (
            not HANDLE_PATTERN.fullmatch(handle)
            or handle.isdigit()
            or folded in GENERIC_FOLDERS
        ):
            continue
        counts[handle] = counts.get(handle, 0) + 1
        platform = _platform_from_ancestors(path)
        if platform is not None:
            platforms.setdefault(handle, set()).add(platform)

    suggestions = [
        SocialLinkSuggestion(
            platform=(
                next(iter(platforms[handle]))
                if len(platforms.get(handle, set())) == 1
                else None
            ),
            handle=handle,
            source_folder=handle,
            media_count=media_count,
        )
        for handle, media_count in counts.items()
    ]
    return sorted(
        suggestions,
        key=lambda item: (-item.media_count, item.handle.casefold()),
    )


def preview_social_url(raw: str):
    """Normalize without writing a cache row or performing network requests."""
    from app.models import SocialBrand
    from app.schemas.person import SocialBrandRead, SocialLinkPreview
    from app.services.social_registry import normalize_social_url, brand_metadata
    values = normalize_social_url(raw)
    metadata = brand_metadata(values['brand_domain'], values['platform'])
    known = bool(metadata['simple_icons_slug'])
    brand = SocialBrand(domain=values['brand_domain'], **metadata,
                        source='simple-icons' if known else None,
                        status='ready' if known else 'pending')
    return SocialLinkPreview(**values, brand=SocialBrandRead.model_validate(brand))


def ensure_social_brand(session, domain: str, platform: str | None = None):
    from app.models import SocialBrand
    from app.services.social_registry import brand_metadata
    brand = session.get(SocialBrand, domain)
    if brand is None:
        metadata = brand_metadata(domain, platform)
        known = bool(metadata['simple_icons_slug'])
        brand = SocialBrand(domain=domain, **metadata,
                            source='simple-icons' if known else None,
                            status='ready' if known else 'pending')
        session.add(brand)
    return brand


def social_link_read(session, link):
    from app.models import SocialBrand
    from app.schemas.person import SocialBrandRead, SocialLinkRead
    result = SocialLinkRead.model_validate(link)
    if link.brand_domain:
        brand = session.get(SocialBrand, link.brand_domain)
        if brand:
            result.brand = SocialBrandRead.model_validate(brand)
    return result


def resolve_social_brand(domain: str):
    """Lazy import keeps normalization and migration entirely offline."""
    from app.services.social_brands import resolve_brand
    resolve_brand(domain)
