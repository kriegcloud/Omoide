#!/usr/bin/env python3
"""Destructively rebuild CLIP vectors. Run with the server stopped.

Callable from a maintenance task only when it owns exclusive pipeline access;
the SQLite write lock guards the task-status check and DDL as one transaction.
Changing model spaces discards embeddings, never media/scenes or face vectors.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

# Permit `python scripts/rebuild_clip_vectors.py` inside /app.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml


def rebuild_clip_vectors(*, target_engine=None, model=None, config_path=None):
    from app.config import settings
    from app.database import engine, create_vec_tables

    target = target_engine if target_engine is not None else engine
    model = model if model is not None else settings.ai.clip_model
    temporary = None
    config_path = Path(config_path) if config_path is not None else None
    # Prepare the replacement before touching any vectors. Preserve unrelated
    # YAML values, and publish by atomic rename only after the DB commits.
    if config_path is not None:
        data = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
        if data is None:
            data = {}
        if not isinstance(data, dict) or not isinstance(data.get('ai', {}), dict):
            raise ValueError('config must contain an ai mapping')
        data.setdefault('ai', {})['clip_model'] = model.model_name
        fd, name = tempfile.mkstemp(prefix='.clip-config-', dir=config_path.parent)
        temporary = Path(name)
        with os.fdopen(fd, 'w') as output:
            yaml.safe_dump(data, output, sort_keys=False)
            output.flush()
            os.fsync(output.fileno())
    try:
        with target.connect() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            try:
                running = conn.exec_driver_sql(
                    "SELECT id FROM processingtask WHERE status='running' "
                    "AND task_type IN ('process_media', 'run_processor') LIMIT 1"
                ).first()
                if running:
                    raise RuntimeError('Refusing CLIP rebuild while a processing task is running')
                for table in ('media_embeddings', 'scene_embeddings'):
                    conn.exec_driver_sql(f'DROP TABLE IF EXISTS {table}')
                create_vec_tables(conn, model)
                conn.exec_driver_sql('UPDATE media SET embeddings_created=0')
                # Scene has no independent completion flag. Clearing its vector
                # table makes EmbeddingExtractor regenerate from stored scenes.
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        if temporary is not None:
            os.replace(temporary, config_path)
            temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    from app.config import settings, get_user_data_path
    from app.config.schema import ClipModel

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=ClipModel, help='OpenCLIP model_name; also updates config.yaml')
    parser.add_argument('--config', type=Path, help='config.yaml path (defaults to active profile)')
    args = parser.parse_args()
    model = args.model or settings.ai.clip_model
    path = (args.config or get_user_data_path() / 'config.yaml') if args.model else None
    rebuild_clip_vectors(model=model, config_path=path)
    print(f'CLIP vectors reset for {model.model_name} ({model.embedding_size} dimensions)')


if __name__ == '__main__':
    main()
