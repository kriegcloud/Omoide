"""Media API, split by concern. All routers share the /api/media prefix; the
list and folder routers come first so their static paths (``/images``,
``/folders``, ...) win over ``/{media_id}``."""

from fastapi import APIRouter

from . import extensions, core, desktop, folders, geotagging, listing, playback, scenes

router = APIRouter()
for _module in (extensions, listing, geotagging, folders, desktop, core, scenes, playback):
    router.include_router(_module.router)

# Preserve callable imports used by local services and integrations.
from .extensions import (
    _require_media_mutations_allowed,
    _media_file_conflict,
    _move_media,
    _rename_media,
    _queue_edited_media,
    _normalize_relative_path,
    _normalized_media_roots,
    _relative_media_path_expr,
    _folder_prefix_clause,
    _is_local_request,
    create_folder,
    bulk_delete_media,
    bulk_move_media,
    move_media,
    rename_media,
    edit_media,
    get_face_crops,
    start_batch_edit,
    get_neighbors,
    read_exif,
)
from .core import (
    get_media,
    delete_media_file,
    delete_media_record,
    get_similar_media,
    update_geolocation,
    update_favorite,
)
from .desktop import (
    _is_within,
    _resolve_media_parent,
    is_local_request,
    _select_in_explorer_windows,
    _open_in_file_browser,
    open_media_folder,
    open_media_file,
)
from .folders import (
    normalize_relative_path,
    _split_relative_path,
    _build_breadcrumbs,
    list_media_folders,
)
from .geotagging import (
    _missing_geo_select,
    _normalize_folder,
    _escape_like,
    _write_block_reason,
    _probe_all,
    get_missing_geo,
    _ancestors,
    get_missing_geo_folders,
)
from .listing import (
    _media_sort_keys,
    list_media,
    list_locations,
    list_images,
    list_videos,
    list_favorites,
)
from .playback import (
    get_converted_copy,
    stream_video,
)
from .scenes import (
    format_timestamp,
    scenes_vtt,
    get_scenes,
    create_scene,
    delete_scene,
)
