"""Person API, split by concern. All routers share the /api/person prefix;
``core`` is included first so its static paths (e.g. ``/all-simple``) win
over ``/{person_id}``."""

from fastapi import APIRouter, Depends
from app.config import require_mutation_allowed

from . import extensions, appearances, core, merging, relationships, timeline

router = APIRouter(dependencies=[Depends(require_mutation_allowed)])
for _module in (extensions, core, timeline, appearances, merging, relationships):
    router.include_router(_module.router)

# Preserve callable imports used by local services and integrations.
from .extensions import (
    _require_person,
    _require_mutations_enabled,
    get_merge_candidates,
    get_pair_decisions,
    create_pair_decision,
    delete_pair_decision,
    _is_local_request,
    _resolve_media_source,
    _available_destination,
    attach_media_to_person_bulk,
    detach_media_from_person_bulk,
    reassign_media_to_person,
    get_all_persons_simple,
    get_social_links,
    add_social_link,
    delete_social_link,
    get_social_link_suggestions,
    set_profile_face,
    _merge_person_into_target,
    _set_person_hidden,
    hide_persons_bulk,
    unhide_persons_bulk,
    hide_person,
    unhide_person,
    _ensure_person_has_no_datasets,
    merge_queue_router,
)
from .appearances import (
    suggest_faces,
    detach_media_from_person,
    get_faces,
    get_appearances,
    export_person_media,
    add_media_appearance,
)
from .core import (
    _person_sort_keys,
    list_persons,
    get_person,
    update_person,
    update_person_favorite,
    auto_profile_face,
    delete_persons_bulk,
    delete_person,
)
from .merging import (
    merge_persons,
    merge_multiple_persons,
    get_similarities,
    auto_merge_similar_persons,
)
from .relationships import (
    get_person_relationships,
)
from .timeline import (
    get_person_timeline,
    create_person_event,
    update_person_event,
    delete_person_event,
)
