const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'); const path = require('node:path'); const ts = require('typescript');
function load(rel, requireMap = {}) {
  const m = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src', rel), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  new Function('module', 'exports', 'require', code)(m, m.exports, id => requireMap[id] ?? {});
  return m.exports;
}
const serverDate = load('utils/serverDate.ts');
test('offsetless server timestamps are UTC (R6-01)', () => {
  assert.equal(serverDate.parseServerDate('2026-11-02T18:14:30.976564').toISOString(), '2026-11-02T18:14:30.976Z');
  assert.equal(serverDate.parseServerDate('2026-11-02 18:14:30').toISOString(), '2026-11-02T18:14:30.000Z');
  assert.equal(serverDate.parseServerDate('2026-11-02T18:14:30+02:00').toISOString(), '2026-11-02T16:14:30.000Z');
  assert.equal(serverDate.parseServerDate('2026-11-02T18:14:30Z').toISOString(), '2026-11-02T18:14:30.000Z');
});
test('an hours-old task is not "just now" regardless of the browser timezone', () => {
  const { formatRelativeTime } = load('utils/taskFormat.ts', { './serverDate': serverDate });
  const finished = new Date(Date.now() - 3 * 3600 * 1000).toISOString().replace('Z', '');
  const text = formatRelativeTime({ finished_at: finished });
  assert.doesNotMatch(text, /just now/);
  assert.match(text, /3\s*h|3 hours/);
});
