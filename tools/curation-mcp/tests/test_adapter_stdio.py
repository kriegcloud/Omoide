"""Real-stdio tests: the SDK client drives the adapter subprocess against a
fixture-mode Omoide curation server.

Run with the adapter's own environment, from `tools/curation-mcp`:

    uv run python -m unittest discover -s tests -t . -v

The fixture server needs the Omoide interpreter and its extra dependency path:

    OMOIDE_TEST_PYTHON=<omoide venv python> OMOIDE_TEST_PYTHONPATH=<extra deps>

Both default to nothing private: the interpreter falls back to the checkout's
own `.venv`, and the extra path is optional.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

TESTS = Path(__file__).resolve().parent
ADAPTER_ROOT = TESTS.parent
WORKTREE = ADAPTER_ROOT.parent.parent
HARNESS = TESTS / 'fixture_app.py'
EXPECTED_TOOLS = {'curation_status', 'datasets_list', 'dataset_get', 'materialize', 'caption_propose',
                  'export_admit', 'export_get', 'export_resume', 'preview'}
FORBIDDEN_FRAGMENTS = ('review', 'accept', 'reject', 'defer', 'enroll', 'register', 'approve')

SERVER: 'FixtureServer | None' = None


def omoide_python() -> str:
    configured = os.environ.get('OMOIDE_TEST_PYTHON')
    if configured:
        return configured
    common = subprocess.run(['git', '-C', str(WORKTREE), 'rev-parse', '--path-format=absolute',
                             '--git-common-dir'], capture_output=True, text=True, check=True).stdout.strip()
    for candidate in (WORKTREE / '.venv/bin/python', Path(common).parent / '.venv/bin/python'):
        if candidate.exists():
            return str(candidate)
    raise unittest.SkipTest('no Omoide interpreter found; set OMOIDE_TEST_PYTHON')


class FixtureServer:
    """Fixture-mode curation API in its own process, on a free loopback port."""

    def __init__(self) -> None:
        self.state = Path(tempfile.mkdtemp(prefix='omoide-mcp-fixture-'))
        environment = {
            'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
            'HOME': os.environ.get('HOME', str(self.state)),
            'XDG_CONFIG_HOME': str(self.state / 'config'),
            'XDG_DATA_HOME': str(self.state / 'data'),
            'XDG_CACHE_HOME': str(self.state / 'cache'),
            'OMOIDE_CURATION_FIXTURES': '1',
        }
        extra = os.environ.get('OMOIDE_TEST_PYTHONPATH')
        if extra:
            environment['PYTHONPATH'] = extra
        self.log = open(self.state / 'server.log', 'w+')
        self.process = subprocess.Popen([omoide_python(), str(HARNESS), str(self.state)],
                                        cwd=str(WORKTREE), env=environment,
                                        stdout=self.log, stderr=subprocess.STDOUT)
        self.ready = self._await_ready()
        self.url = self.ready['url']
        self.dataset_id = self.ready['dataset_id']
        self.source_ids = self.ready['source_ids']
        self.agent_credential = self.ready['credential_files']['agent']
        self.human_credential = self.ready['credential_files']['fixture_human']
        self.leaky_credential = self.ready['world_readable_credential_file']

    def _await_ready(self) -> dict:
        marker = self.state / 'ready.json'
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('fixture server exited: ' + self._tail())
            if marker.exists():
                ready = json.loads(marker.read_text())
                try:
                    with urllib.request.urlopen(ready['url'] + '/api/curation/status', timeout=2) as response:
                        if json.loads(response.read())['enabled']:
                            return ready
                except Exception:
                    pass
            time.sleep(0.25)
        raise RuntimeError('fixture server did not become ready: ' + self._tail())

    def _tail(self) -> str:
        self.log.flush()
        return Path(self.log.name).read_text()[-2000:]

    def token(self, path: str) -> str:
        return Path(path).read_text().strip()

    def human_review_all(self) -> dict:
        """The separate human lane, deliberately not reachable from the adapter."""
        request = urllib.request.Request(
            self.url + '/fixture-control/human-review', method='POST',
            data=json.dumps({'dataset_id': self.dataset_id}).encode(),
            headers={'Content-Type': 'application/json',
                     'Authorization': 'Bearer ' + self.token(self.human_credential)})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())

    def stop(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.log.close()
        shutil.rmtree(self.state, ignore_errors=True)


def setUpModule() -> None:
    global SERVER
    SERVER = FixtureServer()


def tearDownModule() -> None:
    if SERVER is not None:
        SERVER.stop()


@asynccontextmanager
async def adapter(credential_file: str | None, *, url: str | None = None, errlog_path: Path | None = None):
    environment = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                   'OMOIDE_URL': url if url is not None else SERVER.url}
    if credential_file is not None:
        environment['OMOIDE_CURATION_CREDENTIAL_FILE'] = credential_file
    handle = open(errlog_path, 'w+') if errlog_path else open(os.devnull, 'w')
    try:
        params = StdioServerParameters(command=sys.executable, args=['-m', 'omoide_curation_mcp'],
                                       env=environment, cwd=str(ADAPTER_ROOT))
        async with stdio_client(params, errlog=handle) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session
    finally:
        handle.close()


def structured(result) -> dict:
    if result.structured_content is not None:
        return result.structured_content
    texts = [block.text for block in result.content if getattr(block, 'type', None) == 'text']
    return json.loads(texts[-1])


def error_code(result) -> str:
    assert result.is_error, 'expected an error result'
    for block in result.content:
        if getattr(block, 'type', None) == 'text':
            return json.loads(block.text)['error']['code']
    raise AssertionError('no error payload in result')


def run(coroutine) -> None:
    anyio.run(coroutine)


class ToolSurfaceTests(unittest.TestCase):
    def test_exact_tool_surface_without_human_authority(self):
        async def scenario():
            async with adapter(SERVER.agent_credential) as session:
                listed = await session.list_tools()
                names = {tool.name for tool in listed.tools}
                self.assertEqual(names, EXPECTED_TOOLS)
                for tool in listed.tools:
                    words = set(tool.name.split('_'))
                    for fragment in FORBIDDEN_FRAGMENTS:
                        self.assertNotIn(fragment, words,
                                         f'{tool.name} looks like a human-only operation')
                    schema = tool.input_schema
                    self.assertEqual(schema.get('type'), 'object')
                    self.assertFalse(schema.get('additionalProperties', True),
                                     f'{tool.name} input schema must be closed')
                    self.assertTrue(tool.description)
                mutating = {'materialize', 'caption_propose', 'export_admit', 'export_resume'}
                for tool in listed.tools:
                    if tool.name in mutating:
                        self.assertIn('cancellation', tool.description.lower())
                    if tool.name in {'materialize', 'caption_propose', 'export_admit'}:
                        self.assertIn('idempotency_key', tool.input_schema['required'])
        run(scenario)

    def test_unknown_and_human_only_tool_names_are_refused(self):
        async def scenario():
            async with adapter(SERVER.agent_credential) as session:
                for name in ('review', 'review_accept', 'enroll', 'source_register'):
                    result = await session.call_tool(name, {})
                    self.assertEqual(error_code(result), 'unknown_tool')
        run(scenario)

    def test_status_is_reachable_and_declares_no_human_presence(self):
        async def scenario():
            async with adapter(SERVER.agent_credential) as session:
                payload = structured(await session.call_tool('curation_status', {}))
                self.assertTrue(payload['enabled'])
                self.assertEqual(payload['mode'], 'fixture')
                self.assertFalse(payload['generative_enabled'])
                self.assertFalse(payload['human_presence_verified'])
                self.assertFalse(payload['adapter']['review_tools'])
                self.assertFalse(payload['adapter']['enrollment_tools'])
        run(scenario)


class CredentialTests(unittest.TestCase):
    def test_status_works_without_a_credential_and_reads_do_not(self):
        async def scenario():
            async with adapter(None) as session:
                self.assertTrue(structured(await session.call_tool('curation_status', {}))['enabled'])
                result = await session.call_tool('datasets_list', {})
                self.assertEqual(error_code(result), 'credential_file_unconfigured')
        run(scenario)

    def test_world_readable_credential_file_is_refused(self):
        async def scenario():
            async with adapter(SERVER.leaky_credential) as session:
                result = await session.call_tool('datasets_list', {})
                self.assertEqual(error_code(result), 'credential_file_permissions')
        run(scenario)

    def test_plaintext_http_to_a_remote_host_is_refused_at_startup(self):
        process = subprocess.run([sys.executable, '-m', 'omoide_curation_mcp'],
                                 cwd=str(ADAPTER_ROOT), capture_output=True, text=True, timeout=60,
                                 env={'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                                      'OMOIDE_URL': 'http://omoide.example.org',
                                      'OMOIDE_CURATION_CREDENTIAL_FILE': SERVER.agent_credential})
        self.assertEqual(process.returncode, 2)
        self.assertIn('insecure_omoide_url', process.stderr)
        self.assertEqual(process.stdout, '')


class ErrorMappingTests(unittest.TestCase):
    def test_application_codes_are_relayed_verbatim(self):
        async def scenario():
            async with adapter(SERVER.agent_credential) as session:
                detail = structured(await session.call_tool('dataset_get', {'dataset_id': SERVER.dataset_id}))
                stale = await session.call_tool('materialize', {
                    'dataset_id': SERVER.dataset_id, 'source_id': SERVER.source_ids[0],
                    'expected_revision': detail['revision'] + 99, 'idempotency_key': 'stale-revision-key'})
                self.assertEqual(error_code(stale), 'revision_conflict')
                foreign = await session.call_tool('dataset_get', {'dataset_id': 'ffffffffffffffffffffffffffffffff'})
                self.assertEqual(error_code(foreign), 'not_found')
                missing = await session.call_tool('export_get', {'export_id': 'ffffffffffffffffffffffffffffffff'})
                self.assertEqual(error_code(missing), 'not_found')
                bad = await session.call_tool('dataset_get', {'dataset_id': '../../etc/passwd'})
                self.assertEqual(error_code(bad), 'invalid_arguments')
                extra = await session.call_tool('dataset_get', {'dataset_id': SERVER.dataset_id,
                                                                'actor_kind': 'fixture_human'})
                self.assertEqual(error_code(extra), 'invalid_arguments')
                short_key = await session.call_tool('materialize', {
                    'dataset_id': SERVER.dataset_id, 'source_id': SERVER.source_ids[0],
                    'expected_revision': detail['revision'], 'idempotency_key': 'short'})
                self.assertEqual(error_code(short_key), 'invalid_arguments')
                cursor = await session.call_tool('datasets_list', {'cursor': 'not-a-cursor'})
                self.assertEqual(error_code(cursor), 'invalid_cursor')
        run(scenario)

    def test_unreachable_endpoint_is_an_adapter_code(self):
        async def scenario():
            async with adapter(SERVER.agent_credential, url='http://127.0.0.1:1') as session:
                result = await session.call_tool('curation_status', {})
                self.assertEqual(error_code(result), 'omoide_unreachable')
        run(scenario)


class CurationFlowTests(unittest.TestCase):
    """One ordered scenario: the mutating flow, its replays and its receipts."""

    def test_flow(self):
        run(self._flow)

    async def _flow(self):
        errlog = Path(SERVER.state) / 'adapter-flow.log'
        secret = SERVER.token(SERVER.agent_credential)
        transcript: list[str] = []
        async with adapter(SERVER.agent_credential, errlog_path=errlog) as session:
            async def call(name, arguments):
                result = await session.call_tool(name, arguments)
                transcript.append(json.dumps([block.model_dump() for block in result.content], default=str))
                if result.structured_content is not None:
                    transcript.append(json.dumps(result.structured_content, default=str))
                return result

            listing = structured(await call('datasets_list', {'limit': 1}))
            self.assertEqual(listing['total'], 1)
            self.assertEqual(listing['datasets'][0]['id'], SERVER.dataset_id)
            self.assertIsNone(listing['next_cursor'])

            state = structured(await call('dataset_get', {'dataset_id': SERVER.dataset_id}))
            self.assertEqual(state['policy_version'], 'fixture-stills-v1')
            self.assertEqual(state['actor']['kind'], 'agent')
            self.assertNotIn('review', state['actor']['operations'])
            self.assertNotIn('preview', state['actor']['operations'])
            self.assertEqual(state['item_total'], 0)
            self.assertEqual(len(state['sources']), 3)

            # --- materialize, with a durable application idempotency key
            revision = state['revision']
            first = structured(await call('materialize', {
                'dataset_id': SERVER.dataset_id, 'source_id': SERVER.source_ids[0],
                'expected_revision': revision, 'idempotency_key': 'materialize-source-one'}))
            self.assertEqual(first['item']['source_id'], SERVER.source_ids[0])
            self.assertTrue(first['idempotency']['durable'])
            self.assertIn('caption_required', first['item']['blockers'])
            replay = structured(await call('materialize', {
                'dataset_id': SERVER.dataset_id, 'source_id': SERVER.source_ids[0],
                'expected_revision': revision, 'idempotency_key': 'materialize-source-one'}))
            self.assertEqual(replay, first)
            conflict = await call('materialize', {
                'dataset_id': SERVER.dataset_id, 'source_id': SERVER.source_ids[1],
                'expected_revision': revision, 'idempotency_key': 'materialize-source-one'})
            self.assertEqual(error_code(conflict), 'idempotency_conflict')

            for index in (1, 2):
                current = structured(await call('dataset_get', {'dataset_id': SERVER.dataset_id}))
                structured(await call('materialize', {
                    'dataset_id': SERVER.dataset_id, 'source_id': SERVER.source_ids[index],
                    'expected_revision': current['revision'],
                    'idempotency_key': f'materialize-source-{index}'}))

            # --- pagination and cursor binding
            page = structured(await call('dataset_get', {'dataset_id': SERVER.dataset_id, 'limit': 2}))
            self.assertEqual(page['item_total'], 3)
            self.assertEqual(page['item_count'], 2)
            self.assertIsNotNone(page['next_cursor'])
            seen = [row['artifact_id'] for row in page['items']]
            tail = structured(await call('dataset_get', {'dataset_id': SERVER.dataset_id,
                                                         'limit': 2, 'cursor': page['next_cursor']}))
            seen += [row['artifact_id'] for row in tail['items']]
            self.assertEqual(len(set(seen)), 3)
            self.assertIsNone(tail['next_cursor'])
            stale_cursor = page['next_cursor']

            # --- captions: proposals only, with process-local replay protection
            artifacts = seen
            state = structured(await call('dataset_get', {'dataset_id': SERVER.dataset_id, 'limit': 100}))
            captioned = structured(await call('caption_propose', {
                'dataset_id': SERVER.dataset_id, 'artifact_id': artifacts[0],
                'text': 'Generated geometry fixture, blue ellipse on parchment.',
                'expected_revision': state['revision'], 'idempotency_key': 'caption-key-one'}))
            self.assertFalse(captioned['idempotency']['durable'])
            self.assertFalse(captioned['idempotency']['replayed'])
            self.assertIn('review_required', captioned['item']['blockers'])
            caption_replay = structured(await call('caption_propose', {
                'dataset_id': SERVER.dataset_id, 'artifact_id': artifacts[0],
                'text': 'Generated geometry fixture, blue ellipse on parchment.',
                'expected_revision': state['revision'], 'idempotency_key': 'caption-key-one'}))
            self.assertTrue(caption_replay['idempotency']['replayed'])
            self.assertEqual(caption_replay['caption_sha256'], captioned['caption_sha256'])
            caption_conflict = await call('caption_propose', {
                'dataset_id': SERVER.dataset_id, 'artifact_id': artifacts[0],
                'text': 'A different caption under the same key.',
                'expected_revision': state['revision'], 'idempotency_key': 'caption-key-one'})
            self.assertEqual(error_code(caption_conflict), 'idempotency_conflict')
            for index, artifact_id in enumerate(artifacts[1:], start=1):
                current = structured(await call('dataset_get', {'dataset_id': SERVER.dataset_id, 'limit': 100}))
                structured(await call('caption_propose', {
                    'dataset_id': SERVER.dataset_id, 'artifact_id': artifact_id,
                    'text': f'Generated geometry fixture number {index}.',
                    'expected_revision': current['revision'],
                    'idempotency_key': f'caption-key-{index}'}))

            # A cursor minted before those revisions must not silently re-page.
            stale = await call('dataset_get', {'dataset_id': SERVER.dataset_id, 'cursor': stale_cursor})
            self.assertEqual(error_code(stale), 'snapshot_stale')

            # --- the agent cannot make items exportable on its own
            before_review = structured(await call('dataset_get', {'dataset_id': SERVER.dataset_id, 'limit': 100}))
            self.assertTrue(all('review_required' in row['blockers'] for row in before_review['items']))
            blocked = await call('export_admit', {'dataset_id': SERVER.dataset_id,
                                                  'expected_revision': before_review['revision'],
                                                  'idempotency_key': 'export-before-review'})
            # The application's own code, relayed verbatim.
            self.assertEqual(error_code(blocked), 'review_required')

            # --- the human lane acts outside the adapter
            SERVER.human_review_all()
            reviewed = structured(await call('dataset_get', {'dataset_id': SERVER.dataset_id, 'limit': 100}))
            self.assertTrue(all(row['eligible'] for row in reviewed['items']))
            self.assertEqual(reviewed['items'][0]['review']['actor_kind'], 'fixture_human')
            self.assertFalse(reviewed['items'][0]['review']['human_presence_verified'])

            # --- export, receipt, replay and resume
            admitted = structured(await call('export_admit', {
                'dataset_id': SERVER.dataset_id, 'expected_revision': reviewed['revision'],
                'idempotency_key': 'export-after-review'}))
            receipt = admitted['export']
            self.assertEqual(receipt['status'], 'succeeded')
            self.assertEqual(receipt['item_count'], 3)
            self.assertTrue(receipt['manifest_sha256'])
            fetched = structured(await call('export_get', {'export_id': receipt['id']}))
            self.assertEqual(fetched['export'], receipt)
            resumed = structured(await call('export_resume', {'export_id': receipt['id']}))
            self.assertEqual(resumed['export']['id'], receipt['id'])
            self.assertEqual(resumed['export']['manifest_sha256'], receipt['manifest_sha256'])
            replayed_export = structured(await call('export_admit', {
                'dataset_id': SERVER.dataset_id, 'expected_revision': reviewed['revision'],
                'idempotency_key': 'export-after-review'}))
            self.assertEqual(replayed_export['export']['id'], receipt['id'])

            # --- preview under an agent grant: metadata only
            metadata_only = structured(await call('preview', {
                'dataset_id': SERVER.dataset_id, 'target': 'artifact',
                'target_id': artifacts[0], 'include_bytes': True}))
            self.assertFalse(metadata_only['preview_granted'])
            self.assertFalse(metadata_only['disclosure']['image_bytes_returned'])
            self.assertEqual(metadata_only['disclosure']['reason'], 'preview_not_granted')
            self.assertEqual(metadata_only['metadata']['artifact_id'], artifacts[0])

        # --- secret hygiene and stdout discipline
        joined = '\n'.join(transcript)
        self.assertNotIn(secret, joined)
        self.assertGreater(len(joined), 1000)
        stderr_text = errlog.read_text()
        self.assertNotIn(secret, stderr_text)
        self.assertIn('omoide curation adapter', stderr_text)
        self.assertIn('POST /api/curation', stderr_text)

        # --- preview bytes under the reviewer grant, disclosed before delivery
        async with adapter(SERVER.human_credential) as human:
            listed = structured(await human.call_tool('dataset_get', {'dataset_id': SERVER.dataset_id,
                                                                      'limit': 1}))
            self.assertIn('preview', listed['actor']['operations'])
            artifact_id = listed['items'][0]['artifact_id']
            withheld = structured(await human.call_tool('preview', {
                'dataset_id': SERVER.dataset_id, 'target': 'artifact', 'target_id': artifact_id}))
            self.assertTrue(withheld['preview_granted'])
            self.assertFalse(withheld['disclosure']['image_bytes_returned'])
            self.assertEqual(withheld['disclosure']['reason'], 'include_bytes_false')

            result = await human.call_tool('preview', {
                'dataset_id': SERVER.dataset_id, 'target': 'artifact',
                'target_id': artifact_id, 'include_bytes': True})
            self.assertFalse(result.is_error)
            kinds = [block.type for block in result.content]
            self.assertEqual(kinds[0], 'text')
            self.assertIn('image', kinds)
            self.assertLess(kinds.index('text'), kinds.index('image'),
                            'the disclosure must precede the image bytes')
            self.assertIn('DISCLOSURE', result.content[0].text)
            payload = structured(result)
            self.assertTrue(payload['disclosure']['image_bytes_returned'])
            self.assertEqual(payload['disclosure']['media_type'], 'image/png')
            self.assertGreater(payload['disclosure']['byte_count'], 0)
            self.assertLessEqual(payload['disclosure']['byte_count'], payload['disclosure']['max_bytes'])
            image = next(block for block in result.content if block.type == 'image')
            self.assertEqual(image.mime_type, 'image/png')
            import base64
            import hashlib
            self.assertEqual(hashlib.sha256(base64.b64decode(image.data)).hexdigest(),
                             payload['disclosure']['sha256'])

            bounded = await human.call_tool('preview', {
                'dataset_id': SERVER.dataset_id, 'target': 'artifact',
                'target_id': artifact_id, 'include_bytes': True, 'max_bytes': 1024})
            self.assertEqual(error_code(bounded), 'response_too_large')

            source_metadata = structured(await human.call_tool('preview', {
                'dataset_id': SERVER.dataset_id, 'target': 'source',
                'target_id': SERVER.source_ids[0]}))
            self.assertEqual(source_metadata['metadata']['id'], SERVER.source_ids[0])


if __name__ == '__main__':
    unittest.main()
