const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'); const path = require('node:path'); const ts = require('typescript');
function load(rel) { const m = { exports: {} }; new Function('module', 'exports', ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src', rel), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText)(m, m.exports); return m.exports; }
test('clock never shows :60 (R1-01)', () => {
  const { formatClock } = load('utils/duration.ts');
  assert.equal(formatClock(59.49), '0:59');
  assert.equal(formatClock(59.5), '1:00');
  assert.equal(formatClock(59.99), '1:00');
  assert.equal(formatClock(60), '1:00');
  assert.equal(formatClock(119.99), '2:00');
  assert.equal(formatClock(3725), '1:02:05');
  assert.equal(formatClock(undefined), '');
});
test('task durations carry rounded seconds into minutes', () => {
  const src = fs.readFileSync(path.join(__dirname, '../src/utils/taskFormat.ts'), 'utf8');
  const m = { exports: {} }; new Function('module', 'exports', 'require', ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText)(m, m.exports, () => ({}));
  assert.equal(m.exports.formatTaskDuration(119.6), '2 min');
  assert.equal(m.exports.formatTaskDuration(61), '1 min 1 s');
});
