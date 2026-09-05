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
