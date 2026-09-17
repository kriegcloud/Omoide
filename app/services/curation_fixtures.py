"""Trusted local fixture bootstrap; deliberately NOT reachable through HTTP.

Caller owns a fresh temporary SQLite DB and rights-clear paths. Never register a
production library root. Returned random credentials are private fixture state.
"""
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlmodel import Session

from app.config import FaceRecognitionSettings
from app.curation_models import CurationDataset, CurationGrant, CurationSource
from app.services.curation_artifacts import directory, read_at
from app.services.curation_media import VIDEO_EXTENSIONS
from app.services.curation_policy import POLICY_VERSION, digest, fail


def create_fixture_dataset(session: Session, *, source_root: Path, store_root: Path,
                           files: list[dict], name: str = 'Rights-clear still fixtures',
                           face_settings=None, video_frame_materialization: bool = False) -> dict:
    """files: {relative_path,label?,group_id,split?,lineage_known?,generative?}."""
    source_root = Path(os.path.abspath(source_root))
    store_root = Path(os.path.abspath(store_root))
    if source_root == store_root or source_root in store_root.parents or store_root in source_root.parents:
        fail('overlapping_roots')
    if not 1 <= len(files) <= 20:
        fail('fixture_bound_exceeded')
    # The caller creates roots; this helper never mkdirs within an original root.
    with directory(str(source_root)) as source_fd, directory(str(store_root)) as store_fd:
        source_stat = os.fstat(source_fd)
        store_stat = os.fstat(store_fd)
        # Resolve a test configuration, not workstation live settings. A requested
        # loose preset resolving below the protected gates is rejected.
        face = face_settings or FaceRecognitionSettings(preset='normal')
        threshold = face.existing_person_cosine_threshold
        margin = face.existing_person_min_cosine_margin
        if threshold < 0.62 or margin < 0.05:
            fail('threshold_policy_lowered', 403)
        dataset = CurationDataset(name=name, subject_id='rights-clear-fixture-subject',
            source_root=str(source_root), source_device=source_stat.st_dev, source_inode=source_stat.st_ino,
            store_root=str(store_root), store_device=store_stat.st_dev, store_inode=store_stat.st_ino,
            policy={'version': POLICY_VERSION, 'fixture_only': True, 'generative_enabled': False,
                'human_presence_verified': False, 'egress': 'local-fixture-ui-only',
                'identity': {'preset': face.preset.value, 'assignment_threshold': threshold,
                    'assignment_margin': margin, 'matching_enabled': False,
                    'frame_mining_enabled': False, 'frame_mining_gate': None,
                    'outlier_enabled': False, 'outlier_gate': None},
                'lineage_basis': 'trusted fixture registration; no inferred private-media ancestry',
                # Off unless the fixture owner explicitly opts in; no HTTP route
                # can raise this flag after the dataset exists.
                'media': {'video_frame_materialization': video_frame_materialization,
                          'allowed_video_extensions': list(VIDEO_EXTENSIONS),
                          'basis': 'trusted_fixture_registration',
                          'generative_derivatives': False,
                          'repair_and_mask_materialization': False},
                'max_items': 20})
        session.add(dataset)
        session.flush()
        for entry in files:
            data = read_at(source_fd, entry['relative_path'])
            split = entry.get('split', 'train')
            if split not in {'train', 'validation', 'test', 'excluded'}:
                fail('invalid_split')
            session.add(CurationSource(dataset_id=dataset.id, relative_path=entry['relative_path'],
                label=entry.get('label', 'Fixture still'), sha256=digest(data), size=len(data),
                group_id=entry['group_id'], split=split,
                lineage_known=entry.get('lineage_known', True), generative=entry.get('generative', False)))
        credentials = {}
        for kind in ('agent', 'fixture_human'):
            token = secrets.token_urlsafe(32)
            credentials[kind] = token
            operations = ['read', 'materialize', 'caption', 'export']
            if kind == 'fixture_human':
                operations += ['review', 'preview']
            session.add(CurationGrant(token_sha256=digest(token.encode()), actor_id='fixture-' + kind,
                actor_kind=kind, dataset_id=dataset.id, expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=8),
                disclosure=kind == 'fixture_human', operations=operations))
        session.commit()
        return {'dataset_id': dataset.id, 'credentials': credentials}
