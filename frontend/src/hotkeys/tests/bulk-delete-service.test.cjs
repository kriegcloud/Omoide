/* eslint-disable @typescript-eslint/no-require-imports -- Standalone Node contract tests use installed TypeScript without adding a runner. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

function service(fetch) {
  const code = ts.transpileModule(fs.readFileSync(path.resolve(__dirname, '../../services/mediaActions.ts'), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  const module = { exports: {} };
  new Function('require', 'module', 'exports', 'fetch', code)(name => {
    if (name === '../stores/mutationBus') return { mutationBus: { emit() {} } };
    assert.equal(name, '../config');
    return { API: 'https://example.invalid' };
  }, module, module.exports, fetch);
  return module.exports;
}

for (const action of ['DELETE_FILES', 'DELETE_RECORDS', 'BLACKLIST_RECORDS']) {
  test(`bulk-delete service sends the Lane J ${action} contract and retains partial results`, async () => {
    const result = { removed: 1, processed_ids: [11], skipped_ids: [12], errors: [{ id: 13, error: 'Read-only directory' }] };
    const api = service(async (url, options) => {
      assert.equal(url, 'https://example.invalid/api/media/bulk-delete');
      assert.equal(options.method, 'POST');
      assert.equal(options.headers['Content-Type'], 'application/json');
      assert.deepEqual(JSON.parse(options.body), { media_ids: [11, 12, 13], action });
      return { ok: true, json: async () => result };
    });
    assert.deepEqual(await api.bulkDeleteMedia([11, 12, 13], action), result);
  });
}

test('bulk-delete service rejects server failures with the API error', async () => {
  const api = service(async () => ({ ok: false, json: async () => ({ detail: 'Presentation mode is enabled' }) }));
  await assert.rejects(api.bulkDeleteMedia([11], 'DELETE_FILES'), /Presentation mode is enabled/);
});
