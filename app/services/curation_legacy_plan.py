"""Bounded legacy metadata proposals, never registration or review authority.

Only explicitly supplied DatasetItem ids are inspected. No media filesystem
access, writes, dataset census, implicit approval, or lineage inference occurs.
"""
from copy import deepcopy

from sqlmodel import Session, select

from app.models import AnnotationKind, DatasetCaptionSource, DatasetItem, Media, MediaAnnotation, TrainingDataset
from app.services.curation_artifacts import canonical
from app.services.curation_policy import digest, fail


def plan_legacy_dataset_items(session: Session, *, dataset_id: int, item_ids: list[int]) -> dict:
    """Return proposals in requested order from a caller-owned read-only session.

    Operators must independently authorize exact source files, obtain their hashes,
    attest subject and complete nongenerative ancestry, and declare groups/splits.
    A proposal is deliberately not a SourceRegistrationManifest.
    """
    if (type(dataset_id) is not int or dataset_id <= 0 or not isinstance(item_ids, list)
            or not 1 <= len(item_ids) <= 100 or any(type(i) is not int or i <= 0 for i in item_ids)
            or len(set(item_ids)) != len(item_ids)):
        fail('invalid_legacy_plan_selection', 422)
    if session.new or session.dirty or session.deleted:
        fail('legacy_plan_requires_clean_session')
    with session.no_autoflush:
        dataset = session.get(TrainingDataset, dataset_id)
        if dataset is None:
            fail('legacy_dataset_not_found', 404)
        rows = session.exec(select(DatasetItem, Media).join(Media, Media.id == DatasetItem.media_id)
            .where(DatasetItem.dataset_id == dataset_id, DatasetItem.id.in_(item_ids))).all()
        indexed = {item.id: (item, media) for item, media in rows}
        if set(indexed) != set(item_ids):
            fail('legacy_item_selection_unavailable', 404)
        proposals = []
        for item_id in item_ids:
            item, media = indexed[item_id]
            caption = None
            blockers = ['source_hash_not_observed', 'subject_requires_operator_attestation',
                'capture_group_not_attested', 'split_not_attested', 'unknown_ancestry',
                'exact_derivative_review_required']
            if dataset.caption_source == DatasetCaptionSource.NONE:
                blockers.append('legacy_dataset_caption_source_none')
            elif item.caption_override is not None:
                caption = {'text': item.caption_override,
                    'sha256': digest(item.caption_override.encode('utf-8')),
                    'provenance': {'kind': 'legacy_dataset_item_override', 'dataset_id': dataset_id,
                        'item_id': item.id, 'field': 'caption_override'}}
            elif dataset.caption_source == DatasetCaptionSource.TEMPLATE:
                blockers.append('legacy_template_requires_explicit_caption')
            else:
                annotation = session.exec(select(MediaAnnotation).where(
                    MediaAnnotation.media_id == media.id,
                    MediaAnnotation.kind == AnnotationKind.CAPTION).order_by(
                    MediaAnnotation.revision.desc(), MediaAnnotation.id.desc()).limit(1)).first()
                if annotation is not None:
                    content = annotation.content if isinstance(annotation.content, dict) else {}
                    field = 'caption' if isinstance(content.get('caption'), str) else 'text'
                    text = content.get(field)
                    if isinstance(text, str):
                        caption = {'text': text, 'sha256': digest(text.encode('utf-8')),
                            'provenance': {'kind': 'legacy_media_annotation',
                                'annotation_id': annotation.id, 'revision': annotation.revision,
                                'schema_version': annotation.schema_version, 'field': field,
                                'content_sha256': digest(canonical(content)),
                                'provenance_sha256': digest(canonical(annotation.provenance)),
                                'recorded_author': str(annotation.author),
                                'recorded_review_status': str(annotation.review_status)}}
                    else:
                        blockers.append('legacy_caption_not_text')
            if caption is None:
                blockers.append('caption_proposal_unavailable')
            elif not 1 <= len(caption['text']) <= 8192:
                blockers.append('caption_proposal_outside_supported_length')
            if item.edit_ops or item.edit_design_state or item.origin != 'media':
                blockers.append('legacy_edit_or_origin_requires_independent_provenance')
            proposals.append({'legacy_item_id': item.id, 'legacy_media_id': media.id,
                'source_path_proposal': media.path, 'source_sha256': None,
                'caption_proposal': caption,
                'caption_selection_policy': 'override-else-latest-raw-annotation-observation',
                'caption_is_existing_rendered_dataset_caption': False,
                'subject_proposal': {'legacy_person_id': dataset.person_id,
                                     'basis': 'legacy_metadata_only'},
                'capture_group': None, 'split': None,
                'ancestry': {'kind': 'unknown', 'ancestry_complete': False},
                'legacy_metadata': {'excluded': item.excluded, 'origin': item.origin,
                    'edit_ops': deepcopy(item.edit_ops),
                    'has_edit_design_state': bool(item.edit_design_state),
                    'has_reviewed_at': item.reviewed_at is not None,
                    'has_caption_reviewed_at': item.caption_reviewed_at is not None},
                'accepted': False, 'registerable': False, 'blockers': blockers})
    return {'schema_version': 'omoide.legacy-source-plan/v1', 'legacy_dataset_id': dataset_id,
            'legacy_caption_configuration': {'source': str(dataset.caption_source),
                'template': dataset.caption_template, 'trigger_word': dataset.trigger_word,
                'class_token': dataset.class_token},
            'metadata_only': True, 'source_bytes_read': False, 'writes_performed': False,
            'legacy_review_is_authority': False, 'items': proposals}
