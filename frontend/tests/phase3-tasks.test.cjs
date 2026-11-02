const { test } = require('node:test');
const assert = require('node:assert/strict');
const { runtime, load, button, deferred, flush } = require('./datasetRuntime.cjs');
test('repair confirmation dismisses before acknowledgement and snapshots media ids', async () => {
  const rt = runtime(), pending = deferred(), ids = [4], calls = [];
  const Dialog = load('components/RepairDialog.tsx', rt, {
    '../services/repairs': { startRepair: id => { calls.push(id); return pending.promise; }, startBulkRepair: () => pending.promise },
    './UpscalerPicker': { __esModule: true, default: 'UpscalerPicker', defaultUpscaleSelection: () => ({ provider: 'local', params: {}, hostedConsent: false }) },
  }).default;
  const render = () => rt.render(() => Dialog({ open: true, mediaIds: ids, onClose: () => calls.push('close') }));
  button(render(), 'Start').props.onClick();
  assert.equal(calls.includes('close'), true);
  ids[0] = 9; pending.resolve({ id: 'repair' }); await flush();
  assert.equal(calls[0], 'close'); assert.equal(calls[1], 4);
});
function serviceEnvironment() {
  const fs = require('node:fs'), path = require('node:path'), ts = require('typescript');
  const cache = new Map();
  function read(relative) {
    const file = path.resolve(__dirname, '../src', relative);
    if (cache.has(file)) return cache.get(file).exports;
    const module = { exports: {} }; cache.set(file, module);
    const js = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
    new Function('require', 'module', 'exports', js)(name => name === 'react' ? runtime().api : name.startsWith('.') ? read(path.relative(path.resolve(__dirname, '../src'), path.resolve(path.dirname(file), name + '.ts'))) : require(name), module, module.exports);
    return module.exports;
  }
  return { ...read('stores/optimisticOperations.ts'), ...read('services/taskOperations.ts') };
}
test('job acceptance publishes submitting immediately, accepts only after acknowledgement', async () => {
  const e = serviceEnvironment(), pending = deferred();
  const promise = e.submitJob('tasks', 'Scan', () => pending.promise, async () => {});
  assert.equal(e.useJobSubmissions('tasks')[0].phase, 'submitting');
  pending.resolve({ id: 'task' }); assert.deepEqual(await promise, { id: 'task' });
  assert.equal(e.useJobSubmissions('tasks')[0].phase, 'accepted');
});
test('definitive job rejection clears intent and retains global failure feedback', async () => {
  const e = serviceEnvironment();
  await assert.rejects(e.submitJob('tasks', 'Scan', async () => { throw new e.DefinitiveMutationError('denied'); }, async () => {}), /denied/);
  assert.equal(e.useJobSubmissions('tasks')[0].phase, 'failed');
  assert.equal(e.optimisticOperations.getSnapshot()[0].state, 'rejected');
});
test('unknown job outcome revalidates status once and never replays a destructive command', async () => {
  const e = serviceEnvironment(); let writes = 0, reads = 0;
  await assert.rejects(e.submitJob('tasks', 'Cancel', async () => { writes++; throw new TypeError('lost response'); }, async () => { reads++; }), /lost response/);
  await flush(); assert.equal(reads, 1); assert.equal(writes, 1);
  assert.equal(e.useJobSubmissions('tasks')[0].phase, 'checked');
});

test('accepted task status advances from server observations and refresh reaches mounted consumer', async () => {
  const e = serviceEnvironment(); let consumerReads = 0, fallbackReads = 0;
  const release = e.registerJobRefresh('tasks', async () => { consumerReads++; });
  await e.submitJob('tasks', 'Scan', async () => ({ id: 'task', status: 'pending' }), async () => { fallbackReads++; });
  await flush(); assert.equal(consumerReads, 1); assert.equal(fallbackReads, 0);
  e.observeSubmittedJobs('tasks', [{ id: 'task', status: 'running' }]);
  assert.equal(e.useJobSubmissions('tasks')[0].phase, 'running');
  e.observeSubmittedJobs('tasks', [{ id: 'task', status: 'completed' }]);
  assert.equal(e.useJobSubmissions('tasks')[0].phase, 'finished'); release();
});
test('rerun confirmation closes before transport acceptance', async () => {
  const rt = runtime(), pending = deferred(), calls = [];
  const Dialog = load('components/RerunProcessorsDialog.tsx', rt, {
    '../services/taskActions': { runProcessorsForMedia: ids => { calls.push(ids); return pending.promise; } },
  }).RerunProcessorsDialog;
  const render = () => rt.render(() => Dialog({ open: true, mediaIds: [8], onClose: () => calls.push('close'), onStarted: () => calls.push('accepted') }));
  button(render(), 'Run').props.onClick(); assert.deepEqual(calls, ['close']);
  await flush(); pending.resolve({ id: 'task' }); await flush(); assert.deepEqual(calls, ['close', [8], 'accepted']);
});
