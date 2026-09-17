#!/usr/bin/env python3
"""Trusted local source/grant/revocation CLI. Never emits a credential value."""
import argparse
import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True,
                        help='Existing migrated SQLite database; never inferred or created.')
    commands = parser.add_subparsers(dest='command', required=True)
    register = commands.add_parser('register', help='Register only the exact files in an operator-reviewed manifest.')
    register.add_argument('--manifest', type=Path, required=True)
    grant = commands.add_parser('grant', help='Create one dataset-scoped grant and a private token file.')
    grant.add_argument('--dataset-id', required=True)
    grant.add_argument('--actor-id', required=True)
    grant.add_argument('--actor-kind', choices=('human', 'agent'), required=True)
    grant.add_argument('--credential-file', type=Path, required=True)
    grant.add_argument('--lifetime-seconds', type=int, default=28800)
    revoke = commands.add_parser('revoke', help='Revoke a grant or one enrolled credential.')
    revoke.add_argument('--grant-id', required=True)
    revoke.add_argument('--credential-id')
    verify = commands.add_parser('verify', help='Read-only: does this runtime still observe the registered volume and bytes?')
    verify.add_argument('--dataset-id', required=True)
    reattest = commands.add_parser('reattest', help='Audited re-pin of runtime mount identity after a restart or re-plug; every file is re-hashed first.')
    reattest.add_argument('--dataset-id', required=True)
    reattest.add_argument('--operator-id', required=True)
    reattest.add_argument('--statement', required=True,
                          help='Why the runtime changed (for example "container restarted 2026-09-17"). Recorded in the dataset audit.')
    args = parser.parse_args()
    if os.environ.get('OMOIDE_CURATION_MODE') != 'production':
        parser.error('Set OMOIDE_CURATION_MODE=production for this explicitly targeted local operation.')
    database = args.database.absolute()
    try:
        if not stat.S_ISREG(database.lstat().st_mode) or database.is_symlink():
            parser.error('The database must be an existing regular file, not a symlink.')
    except OSError:
        parser.error('The explicitly selected database is unavailable.')

    from fastapi import HTTPException
    from sqlalchemy import text
    from sqlalchemy.engine import URL
    from sqlmodel import Session, create_engine
    from app.services.curation_auth import issue_production_grant, revoke_production_authority

    # SQLite mode=rw prevents creating a new database if it disappears after stat.
    engine = create_engine(URL.create('sqlite', database=database.as_uri(),
                                     query={'mode': 'rw', 'uri': 'true'}))
    try:
        with engine.connect() as connection:
            heads = connection.execute(text('SELECT version_num FROM alembic_version')).scalars().all()
            if heads != ['60718293a4b5']:
                parser.error('The database must have the reviewed curation authority migration; this CLI never migrates it.')
        with Session(engine) as session:
            if args.command == 'register':
                from app.services.curation_registration import register_source_manifest
                with args.manifest.open('rb') as handle:
                    raw = handle.read(262145)
                if len(raw) > 262144:
                    parser.error('The registration manifest exceeds the 256 KiB bound.')
                result = register_source_manifest(session, json.loads(raw))
            elif args.command == 'grant':
                operations = ['read', 'materialize', 'caption', 'export']
                if args.actor_kind == 'human':
                    operations += ['review', 'enroll', 'preview']
                result = issue_production_grant(session, dataset_id=args.dataset_id,
                    actor_id=args.actor_id, actor_kind=args.actor_kind,
                    operations=operations, credential_file=args.credential_file,
                    disclosure=args.actor_kind == 'human', lifetime_seconds=args.lifetime_seconds)
            elif args.command == 'verify':
                from app.services.curation_registration import verify_dataset_volume
                result = verify_dataset_volume(session, args.dataset_id)
            elif args.command == 'reattest':
                from app.services.curation_registration import reattest_source_volume
                result = reattest_source_volume(session, args.dataset_id,
                    {'operator_id': args.operator_id, 'statement': args.statement})
            else:
                result = revoke_production_authority(session, grant_id=args.grant_id,
                                                     credential_id=args.credential_id)
        print(json.dumps({'ok': True, 'result': result}))
    except HTTPException as exc:
        print(json.dumps({'ok': False, 'error': exc.detail}), file=sys.stderr)
        return 1
    except Exception:
        # Do not serialize database rows, credential payloads, or exception locals.
        print(json.dumps({'ok': False, 'error': 'local_authority_operation_failed'}), file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
