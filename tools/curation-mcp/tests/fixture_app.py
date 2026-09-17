"""Task-owned fixture-mode curation server for the adapter's stdio tests.

Runs under the Omoide virtualenv, never under the adapter's. It assembles the
repository's real curation router, guard and services over a temporary SQLite
database, exactly as `tests/test_curation_still_slice.py` does, and serves it
with uvicorn on a pre-bound loopback socket with the application lifespan off.

It touches no live database, no library media and no running container. The
images are generated geometry created here.

`/fixture-control/human-review` exists only in this harness and stands in for
the separate human review lane (the passkey UI). It is not part of the
application, and it is deliberately unreachable from the MCP adapter: the
adapter exposes no review tool and its agent grant carries no review operation.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

STATE = Path(sys.argv[1]).resolve()
WORKTREE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(WORKTREE))
os.chdir(WORKTREE)
os.environ['OMOIDE_CURATION_FIXTURES'] = '1'
os.environ.pop('IS_DOCKER', None)
os.environ.pop('OMOIDE_CURATION_MODE', None)

import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

from app.api.curation import router  # noqa: E402
from app.config import settings  # noqa: E402
from app.curation_models import CurationSource  # noqa: E402
from app.database import get_session  # noqa: E402
from app.schemas.curation import ReviewInput  # noqa: E402
from app.services.curation_fixtures import create_fixture_dataset  # noqa: E402
from app.services.curation_plans import detail, review  # noqa: E402
from app.services.curation_policy import install_curation_guard, mode  # noqa: E402

SHAPES = (('#3366aa', 'ellipse'), ('#aa6633', 'rectangle'), ('#33aa66', 'ellipse'))


def draw_fixture(path: Path, colour: str, shape: str) -> None:
    """Deterministic generated geometry, large enough to exercise the byte bound."""
    image = Image.new('RGB', (256, 192), '#e8eadb')
    canvas = ImageDraw.Draw(image)
    for row in range(0, 192, 12):
        for column in range(0, 256, 12):
            if (row + column) % 24:
                canvas.rectangle((column, row, column + 7, row + 7), fill='#c8ccb4')
    getattr(canvas, shape)((40, 30, 216, 162), fill=colour)
    image.save(path)


def write_secret(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        handle.write(value + '\n')


def main() -> None:
    assert mode() == 'fixture', 'the harness must run in fixture mode'
    settings.general.presentation_mode = False
    sources = STATE / 'sources'
    store = STATE / 'store'
    sources.mkdir()
    store.mkdir()
    files = []
    for index, (colour, shape) in enumerate(SHAPES):
        name = f'still-{index}.png'
        draw_fixture(sources / name, colour, shape)
        files.append({'relative_path': name, 'label': f'Generated geometry {index}',
                      'group_id': f'capture-{index}'})

    engine = create_engine('sqlite:///' + str(STATE / 'fixture.sqlite'),
                           connect_args={'timeout': 20, 'check_same_thread': False})

    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')

    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seeded = create_fixture_dataset(session, source_root=sources, store_root=store, files=files)
        source_ids = [row.id for row in session.exec(select(CurationSource).order_by(CurationSource.id)).all()]

    credentials = {}
    for kind, token in seeded['credentials'].items():
        path = STATE / (kind + '.token')
        write_secret(path, token)
        credentials[kind] = str(path)
    # A deliberately group/world-readable copy, for the refusal test.
    leaky = STATE / 'world-readable.token'
    write_secret(leaky, seeded['credentials']['agent'])
    leaky.chmod(0o644)

    app = FastAPI()
    install_curation_guard(app)
    app.include_router(router, prefix='/api/curation')

    def session_override():
        with Session(engine) as scoped:
            yield scoped

    app.dependency_overrides[get_session] = session_override

    @app.post('/fixture-control/human-review')
    async def human_review(request: Request):
        """Stand-in for the human passkey review lane, outside the adapter."""
        token = request.headers.get('authorization', '')[7:]
        body = await request.json()
        dataset_id = body['dataset_id']
        with Session(engine) as scoped:
            state = detail(scoped, token, dataset_id)
            targets = [item for item in state['items'] if item['caption']]
            for item in targets:
                state = review(scoped, token, dataset_id, ReviewInput(
                    artifact_id=item['artifact_id'], caption_id=item['caption']['id'],
                    asset_sha256=item['sha256'], caption_sha256=item['caption']['sha256'],
                    decision=body.get('decision', 'accept'), expected_revision=state['revision']))
            return {'revision': state['revision'], 'reviewed': len(targets)}

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', 0))
    listener.listen(64)
    port = listener.getsockname()[1]
    ready = {'port': port, 'url': f'http://127.0.0.1:{port}', 'dataset_id': seeded['dataset_id'],
             'source_ids': source_ids, 'credential_files': credentials,
             'world_readable_credential_file': str(leaky), 'state_dir': str(STATE)}
    (STATE / 'ready.json.tmp').write_text(json.dumps(ready))
    os.replace(STATE / 'ready.json.tmp', STATE / 'ready.json')
    server = uvicorn.Server(uvicorn.Config(app, lifespan='off', access_log=False, log_level='warning'))
    server.run(sockets=[listener])


if __name__ == '__main__':
    main()
