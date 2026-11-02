const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
const { deferred, flush } = require('./datasetRuntime.cjs');
function setup() {
  const source = fs.readFileSync(require.resolve('../src/services/config.ts'), 'utf8').replaceAll('import.meta.env.DEV', 'true');
  const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
  const mod = { exports: {} }; const requests = [];
  const operations = [];
  new Function('require', 'module', 'exports', 'fetch', js)(name => name === '../config' ? { API: '' } : {
    DefinitiveMutationError: class DefinitiveMutationError extends Error {},
    startOperation(command) { operations.push(command); const done = Promise.resolve().then(() => command.request(command.input, 'op')).catch(() => {}); return { id: 'op', done }; },
  }, mod, mod.exports, (url, options) => { const d = deferred(); requests.push({ url, options, ...d }); return d.promise; });
  return { ...mod.exports, requests, operations };
}
const config = value => ({ general: { presentation_mode: true, media_dirs: [], value } });
const ok = body => ({ ok: true, json: async () => body });
test('a delayed save keeps later edits and records accepted/applied versions separately', async () => {
  const s = setup(); s.initializeConfigDraft(config(1)); s.updateConfigDraft(config(2));
  s.submitConfigDraft(config(2)); await flush();
  assert.equal(s.getConfigDraftState().phase, 'saving');
  s.updateConfigDraft(config(3));
  s.requests[0].resolve(ok(config(2))); await flush();
  assert.equal(s.getConfigDraftState().accepted.general.value, 2);
  assert.equal(s.getConfigDraftState().draft.general.value, 3);
  assert.equal(s.getConfigDraftState().phase, 'applying');
  s.requests[1].resolve(ok({})); await flush();
  assert.equal(s.getConfigDraftState().applied.general.value, 2);
  assert.equal(s.getConfigDraftState().draft.general.value, 3);
  assert.equal(s.getConfigDraftState().phase, 'applied');
});
test('reload failure retains saved configuration and the editable draft across remount initialization', async () => {
  const s = setup(); s.initializeConfigDraft(config(1)); s.updateConfigDraft(config(2));
  s.submitConfigDraft(config(2)); await flush();
  s.requests[0].resolve(ok(config(2))); await flush();
  s.requests[1].resolve({ ok: false }); await flush();
  s.initializeConfigDraft(config(99));
  assert.equal(s.getConfigDraftState().phase, 'apply-failed');
  assert.equal(s.getConfigDraftState().accepted.general.value, 2);
  assert.equal(s.getConfigDraftState().draft.general.value, 2);
  assert.equal(s.getConfigDraftState().applied.general.value, 1);
});
test('same-tick duplicate submit is blocked and acknowledgement is snapshotted', async () => {
  const s = setup(); s.initializeConfigDraft(config(1));
  assert.equal(s.submitConfigDraft(config(2), true), true);
  assert.equal(s.submitConfigDraft(config(3)), false); await flush();
  assert.equal(s.requests.length, 1);
  assert.equal(JSON.parse(s.requests[0].options.body).acknowledge_media_dir_removals, true);
  assert.equal(s.operations[0].resources[0], 'config:active');
});
test('configuration form and tabs remain interactive during ordinary saves', () => {
  const source = fs.readFileSync(require.resolve('../src/pages/ConfigurationPage.tsx'), 'utf8');
  assert.doesNotMatch(source, /disabled=\{isSaving\}\s*\{\.\.\.\(isSaving \? \{ inert/);
});

test('unknown save outcome retains the draft and never automatically replays the write', async () => {
  const s = setup(); s.initializeConfigDraft(config(1)); s.updateConfigDraft(config(2));
  s.submitConfigDraft(config(2)); await flush();
  s.requests[0].reject(new Error('connection lost')); await flush();
  assert.equal(s.getConfigDraftState().phase, 'save-failed');
  assert.equal(s.getConfigDraftState().draft.general.value, 2);
  assert.equal(s.submitConfigDraft(config(2)), false);
  assert.equal(s.requests.length, 1);
});
test('validation rejection preserves edits and allows a corrected submission', async () => {
  const s = setup(); s.initializeConfigDraft(config(1)); s.updateConfigDraft(config(2));
  s.submitConfigDraft(config(2)); await flush();
  s.requests[0].resolve({ ok: false, status: 422, json: async () => ({ detail: [] }) }); await flush();
  assert.equal(s.getConfigDraftState().phase, 'idle');
  assert.equal(s.getConfigDraftState().draft.general.value, 2);
  assert.equal(s.submitConfigDraft(config(3)), true);
});

test('profile switch activates the target without applying the old presentation-mode configuration first', async () => {
  const s = setup(); s.initializeConfigDraft(config(1));
  s.submitConfigDraft(config(2), false, '/target'); await flush();
  s.requests[0].resolve(ok(config(2))); await flush();
  assert.equal(s.requests[1].url, '/api/config/profiles/switch');
  assert.equal(JSON.parse(s.requests[1].options.body).path, '/target');
  s.requests[1].resolve(ok({})); await flush();
  assert.equal(s.getConfigDraftState().phase, 'profile-switched');
  assert.equal(s.getConfigDraftState().applied.general.value, 1);
  assert.equal(s.requests.length, 2);
});
