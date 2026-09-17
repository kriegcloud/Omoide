const assert = require('node:assert/strict');
const { test } = require('node:test');
const { runtime, load } = require('./datasetRuntime.cjs');

const service = load('services/datasets.ts', runtime());
const respond = (status, body) => async () => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

test('authority denials render as readable text, not [object Object]', async () => {
  globalThis.fetch = respond(403, { detail: { code: 'legacy_human_authority_unavailable' } });
  await assert.rejects(service.reviewDatasetItem(1, 2), (error) => {
    assert.match(error.message, /Legacy review stamping is disabled/);
    assert.match(error.message, /passkey/);
    assert.doesNotMatch(error.message, /object Object/);
    return true;
  });
});

test('describeApiDetail keeps string details, names unknown codes, joins validation lists', () => {
  assert.equal(service.describeApiDetail('Dataset not found', 404), 'Dataset not found');
  assert.equal(service.describeApiDetail({ code: 'source_volume_changed' }, 409), 'Request failed (source_volume_changed)');
  assert.equal(service.describeApiDetail({ message: 'busy' }, 409), 'busy');
  assert.equal(service.describeApiDetail([{ msg: 'field required', loc: ['body', 'x'] }, { msg: 'bad' }], 422), 'field required; bad');
  assert.equal(service.describeApiDetail(undefined, 500), 'Request failed (500)');
  assert.equal(service.describeApiDetail({}, 502), 'Request failed (502)');
});

test('non-JSON failures still report the status', async () => {
  globalThis.fetch = async () => new Response('gateway down', { status: 502 });
  await assert.rejects(service.updateDatasetItem(1, 2, { excluded: true }), /Request failed \(502\)/);
});
