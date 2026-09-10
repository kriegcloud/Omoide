"""Shared browsing visibility and literal folder predicates."""
import os

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, true

from app.config import settings
from app.models import Media


def exclude_missing(stmt):
    """Keep missing records accessible only through detail and missing review."""
    return stmt.where(Media.missing_since.is_(None))


def _folder_tree(folder: str, *, recursive: bool):
    exact = Media.folder == folder
    if not recursive:
        return exact
    prefix = folder.rstrip("/") + "/" if folder else ""
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    descendants = Media.folder.like(escaped + "%", escape="\\")
    if os.name != "nt":
        # SQLite LIKE folds ASCII case even for a binary column. Keep its
        # escaped prefix match, but require the actual POSIX directory spelling.
        descendants = and_(descendants, func.substr(Media.folder, 1, len(prefix)) == prefix)
    return or_(exact, descendants)


def folder_filter(folder: str | None, *, recursive: bool = True):
    """Match an absolute folder or a root-relative folder and its descendants.

    Folder picker values are relative to configured roots; absolute values also
    support links from review summaries. LIKE wildcards are always literal.
    """
    if folder is None:
        return true()
    normalized = folder.replace("\\", "/")
    if any(part in {".", ".."} for part in normalized.split("/")):
        raise HTTPException(status_code=400, detail="Invalid folder path")
    absolute = normalized.startswith("/") or normalized[1:3] == ":/"
    normalized = normalized.rstrip("/") or ("/" if absolute else "")
    if absolute:
        return _folder_tree(normalized, recursive=recursive)
    relative = normalized.strip("/")
    clauses = [_folder_tree(relative, recursive=recursive)] if relative or not recursive else []
    for root, _ in settings.general.resolved_media_dirs():
        root_path = os.fspath(root).replace("\\", "/").rstrip("/")
        target = f"{root_path}/{relative}" if relative else root_path or "/"
        clauses.append(_folder_tree(target, recursive=recursive))
    return or_(*clauses) if clauses else true()
