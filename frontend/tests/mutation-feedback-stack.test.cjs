const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
function load() {
  const file = path.join(__dirname, '../src/components/MutationFeedback.tsx');
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText;
  const module = { exports: {} };
  const stub = { optimisticOperations: { subscribe: () => () => {}, getSnapshot: () => [], dismiss() {} }, reconcileOperation() {}, hasOperationLookup: () => false, canRefreshOperation: () => false, refreshOperation() {} };
  new Function('require', 'module', 'exports', code)(id => id === '../stores/optimisticOperations' ? stub : id === 'react' ? { useEffect() {}, useSyncExternalStore: () => [] } : id === 'react/jsx-runtime' ? { jsx: () => null, jsxs: () => null } : {}, module, module.exports);
  return module.exports;
}
const rec = (id, extra = {}) => ({ id, label: 'Delete selected media', state: 'committed', dismissed: false, ...extra });

test('identical successes collapse into one notice with a count', () => {
  const { feedbackGroups } = load();
  const { visible, hidden } = feedbackGroups([rec('a'), rec('b'), rec('c')]);
  assert.equal(visible.length, 1); assert.equal(visible[0].records.length, 3); assert.equal(hidden, 0);
});

test('successes auto-close; failures, partial and uncertain outcomes stay', () => {
  const { autoCloses } = load();
  assert.equal(autoCloses(rec('a')), true);
  assert.equal(autoCloses(rec('b', { state: 'rejected', error: 'x' })), false);
  assert.equal(autoCloses(rec('c', { state: 'unknown' })), false);
  assert.equal(autoCloses(rec('d', { state: 'partially_committed' })), false);
  assert.equal(autoCloses(rec('e', { state: 'pending' })), false);
});

test('attention-needing notices come first and at most three show', () => {
  const { feedbackGroups } = load();
  const records = [rec('ok1'), rec('f1', { label: 'Tag', state: 'rejected', error: 'no' }), rec('u1', { state: 'unknown' }), rec('u2', { state: 'unknown' }), rec('p1', { label: 'Move', state: 'pending' })];
  const { visible, hidden } = feedbackGroups(records);
  assert.equal(visible.length, 3); assert.equal(hidden, 2);
  assert.ok(visible.every(group => group.lead.state !== 'committed'), 'the success is the one held back');
});

test('dismissed and reconciled records are not shown', () => {
  const { feedbackGroups } = load();
  assert.equal(feedbackGroups([rec('a', { dismissed: true }), rec('b', { state: 'reconciled' })]).visible.length, 0);
});
