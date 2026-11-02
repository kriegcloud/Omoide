const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
function environment() {
  const cache = new Map();
  function load(relative) {
    const file = path.resolve(__dirname, '../src', relative);
    if (cache.has(file)) return cache.get(file).exports;
    const module = { exports: {} }; cache.set(file, module);
    const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText;
    new Function('require', 'module', 'exports', code)(id => id.endsWith('/config') ? { API: '' } : id.startsWith('.') ? load(path.relative(path.resolve(__dirname, '../src'), path.resolve(path.dirname(file), id + '.ts'))) : require(id), module, module.exports);
    return module.exports;
  }
  return { ...load('services/personActions.ts'), ...load('stores/optimisticOperations.ts') };
}
const tick = () => new Promise(setImmediate);
test('people command publishes intent immediately and restores actual skipped ids', async () => {
  const e = environment(); let resolve;
  const op = e.startPeopleMutation('Hide people', [1, 2], () => new Promise(r => { resolve = r; }), { query: 'people-grid-all', applied: r => r.hidden_ids });
  assert.deepEqual(e.projectList('people-grid-all', 'person', [{ id: 1 }, { id: 2 }]).items, []);
  await tick(); resolve({ hidden_ids: [1], skipped_ids: [2] });
  const record = await op.done;
  assert.equal(record.state, 'partially_committed');
  assert.deepEqual(record.receipt.applied, ['person:1']);
  assert.deepEqual(e.projectList('people-grid-all', 'person', [{ id: 1 }, { id: 2 }]).items, [{ id: 2 }]);
  assert.equal(e.projectList('people-grid-hidden', 'person', [{ id: 1 }]).items.length, 1);
});
test('overlapping people commands serialize but unrelated people remain usable', async () => {
  const e = environment(), calls = []; let resolve;
  const a = e.startPeopleMutation('A', [1], () => { calls.push('a'); return new Promise(r => { resolve = r; }); });
  const b = e.startPeopleMutation('B', [1], async () => { calls.push('b'); });
  const c = e.startPeopleMutation('C', [2], async () => { calls.push('c'); });
  await tick(); assert.deepEqual(calls, ['a', 'c']);
  resolve(); await Promise.all([a.done, b.done, c.done]); assert.deepEqual(calls, ['a', 'c', 'b']);
});
test('lost people response stays unknown and never replays destructive request', async () => {
  const e = environment(); let calls = 0;
  const op = e.startPeopleMutation('Merge', [1], async () => { calls++; throw new TypeError('lost response'); });
  assert.equal((await op.done).state, 'unknown'); await tick(); assert.equal(calls, 1);
});
test('assignment dialog closes before server response and does not fabricate reassignment success', async () => {
  const { runtime, load, nodes, deferred, flush } = require('./datasetRuntime.cjs');
  const rt = runtime(), pending = deferred(), log = [];
  const Component = load('components/AssignMediaToPersonDialog.tsx', rt, {
    '../services/personActions': { reassignMediaToPerson: () => pending.promise, startPeopleMutation: (_label, _ids, request, options) => ({ done: request().then(async () => { await options.refresh(); return { state: 'committed', receipt: { applied: [] } }; }) }) },
  }).default;
  const tree = rt.render(() => Component({ open: true, mediaIds: [10], sourcePersonId: 2, onClose: () => log.push('close'), onAssigned: (_p, skipped) => log.push(skipped) }));
  nodes(tree, n => n.props?.onSelect)[0].props.onSelect({ id: 8 });
  assert.deepEqual(log, ['close']);
  pending.resolve({ reassigned: false }); await flush(); assert.deepEqual(log, ['close', 1]);
});
test('message-only face acknowledgement stays unknown without inventing ownership', async () => {
  const e = environment();
  // Load face service with the real command core and inspect committed bus events.
  const { runtime, load } = require('./datasetRuntime.cjs'); const events = [];
  const f = load('services/faceActions.ts', runtime(), {
    '../stores/optimisticOperations': e, '../stores/mutationBus': { mutationBus: { emit: x => events.push(x) } },
  });
  const original = global.fetch; global.fetch = async () => ({ ok: true, json: async () => ({ message: 'Faces assigned successfully' }) });
  try {
    const op = f.startFaceMutation('Assign faces', [1, 404], () => f.assignFace([1, 404], 8, "manual", "orphan-face"), 'source-faces', [8]);
    assert.deepEqual(e.projectList('source-faces', 'face', [{ id: 1 }, { id: 404 }]).items, []);
    const record = await op.done;
    assert.equal(record.state, "unknown");
    assert.equal(record.receipt, undefined);
    assert.equal(events.some(x => x.type === 'face:assigned'), false);
    assert.equal(events.some(x => x.type === 'list:invalidate' && x.prefix === ''), false);
  } finally { global.fetch = original; }
});
test('person detail assignment returns without waiting for transport or five refreshes', async () => {
  const { hooks, load, listStore, noop, deferred, settle } = require('./detailRuntime.cjs');
  const rt = hooks(), store = listStore(), pending = deferred(), e = environment();
  const hook = load('hooks/usePersonDetailPage.ts', rt, {
    '../stores/optimisticOperations': e,
    '../services/person': { getPerson: async () => ({ id: 1 }), getPersonMediaAppearances: async () => ({ items: [] }) },
    '../services/personActions': { getSuggestedFaces: async () => [], getPersonFaces: async () => ({ items: [] }) },
    '../services/faceActions': { assignFace: () => pending.promise, startFaceMutation: (_l, _ids, request) => e.startOperation({ label: 'Assign', input: {}, resources: [], patches: [], request }) },
    '../stores/useListStore': store.module,
    'react-router-dom': { useParams: () => ({ id: '1' }), useLocation: () => ({ state: {} }), useNavigate: () => noop },
  }).usePersonDetailPage;
  global.window = { addEventListener: noop, removeEventListener: noop };
  rt.render(hook); await settle(); let done = false;
  Promise.resolve(rt.render(hook).handleAssignWrapper([4], 8)).then(() => { done = true; });
  await settle(); assert.equal(done, true);
  pending.resolve(); await settle(); rt.unmount();
});
test('favorite intent survives stale props and reports definitive failure globally', async () => {
  const { hooks, load, find, deferred, settle } = require('./detailRuntime.cjs');
  const rt = hooks(), e = environment(), pending = deferred();
  const Card = load('components/PersonCard.tsx', rt, {
    '../stores/optimisticOperations': e,
    '../services/personActions': { startPeopleMutation: e.startPeopleMutation, setPersonFavorite: () => pending.promise },
    '../hooks/useFaceThumbnail': { useFaceThumbnail: () => ({ src: null, aspectRatio: 1 }) },
  }).default;
  const render = () => rt.render(() => Card({ person: { id: 1, name: 'One', is_favorite: false } }));
  let tree = render();
  const favorite = find(tree.props.menu, n => n.props?.['aria-pressed'] !== undefined);
  favorite.props.onClick({ preventDefault() {}, stopPropagation() {} });
  tree = render(); assert.equal(find(tree.props.menu, n => n.props?.['aria-pressed'] !== undefined).props['aria-pressed'], true);
  assert.equal(e.optimisticOperations.getSnapshot().length, 1);
  pending.reject(new e.DefinitiveMutationError('Rejected before commit')); await settle();
  assert.equal(e.optimisticOperations.getSnapshot()[0].state, 'rejected');
  assert.equal(find(render().props.menu, n => n.props?.['aria-pressed'] !== undefined).props['aria-pressed'], false);
  rt.unmount();
});
test('media reassignment to different targets shares the media ownership lock', async () => {
  const e = environment(), calls = []; let resolve;
  const first = e.startPeopleMutation('Assign A', [1], () => { calls.push('a'); return new Promise(r => { resolve = r; }); }, { entityResources: ['media:10'] });
  const second = e.startPeopleMutation('Assign B', [2], async () => { calls.push('b'); }, { entityResources: ['media:10'] });
  await tick(); assert.deepEqual(calls, ['a']); resolve();
  await Promise.all([first.done, second.done]); assert.deepEqual(calls, ['a', 'b']);
});
test('explicit rejection restores only its face overlay; transport loss retains unknown intent', async () => {
  const e = environment(), { runtime, load } = require('./datasetRuntime.cjs');
  const f = load('services/faceActions.ts', runtime(), { '../stores/optimisticOperations': e });
  const a = f.startFaceMutation('Delete', [1], async () => { throw new e.DefinitiveMutationError('Pre-commit rejection'); }, 'faces');
  const b = f.startFaceMutation('Detach', [2], async () => { throw new TypeError('Lost response'); }, 'faces');
  await Promise.all([a.done, b.done]);
  assert.deepEqual(e.projectList('faces', 'face', [{ id: 1 }, { id: 2 }]).items, [{ id: 1 }]);
  assert.deepEqual(e.optimisticOperations.getSnapshot().map(x => x.state), ['rejected', 'unknown']);
});
test('people confirmation snapshots targets and closes while deletion is pending', async () => {
  const { hooks, load, listStore, find, noop, deferred, settle } = require('./detailRuntime.cjs');
  const rt = hooks(), store = listStore(), e = environment(), pending = deferred(), calls = [];
  const key = 'people-grid-all-appearances_desc-'; let selectedIds = new Set([1]);
  store.state.lists[key] = { items: [{ id: 1 }, { id: 2 }], hasMore: false, isLoading: false };
  const Page = load('components/PeopleGrid.tsx', rt, {
    'react-router-dom': { useNavigate: () => noop },
    '../config': { __esModule: true, default: { ENABLE_PEOPLE: true } },
    '../stores/useListStore': { ...store.module, useOptimisticCachedList: key => ({ ...store.state.lists[key], ...e.projectList(key, 'person', store.state.lists[key].items) }) },
    '../hooks/usePeopleSelection': { usePeopleSelection: () => ({ selectionMode: true, selectedIds, enterMode: noop, exitMode: noop, toggleMode: noop, clear: noop, setSelected: noop, pruneTo: noop }) },
    '../hooks/useMarqueeSelection': { useGridSelection: () => ({}) },
    '../hooks/useLocalStoragePreference': { useLocalStoragePreference: () => ['appearances_desc', noop] },
    './PersonThumbSize': { usePersonThumbSize: () => ['medium', noop], PersonThumbSizeToggle: 'PersonThumbSizeToggle' },
    '../services/personActions': { startPeopleMutation: e.startPeopleMutation, deletePersonsBulk: ids => { calls.push(ids); return pending.promise; } },
  }).default;
  const render = () => rt.render(() => Page({ title: 'People', listKey: 'people-grid-all' }));
  find(render(), n => n.props?.children === 'Delete Selected').props.onClick();
  selectedIds = new Set([2]);
  let confirm = find(render(), n => n.props?.title === 'Delete Selected People');
  confirm.props.onConfirm();
  assert.equal(find(render(), n => n.props?.title === 'Delete Selected People').props.open, false);
  await tick(); assert.deepEqual(calls, [[1]]);
  pending.resolve({ deleted_ids: [1], skipped_ids: [] }); await settle(); rt.unmount();
});

test('atomic person face failures restore immediately and revalidate only uncertain failures', async () => {
  for (const kind of ['deleteFace', 'detachFace', 'assignFace']) {
    for (const status of [403, 404, 422, 500, 'transport', 'error']) {
      const e = environment(), { runtime, load } = require('./datasetRuntime.cjs');
      const f = load('services/faceActions.ts', runtime(), { '../stores/optimisticOperations': e });
      let refreshes = 0, errors = 0;
      e.registerQueryRefresh('person-faces', async () => { refreshes++; });
      const original = global.fetch;
      global.fetch = async () => {
        if (status === 'transport') throw new TypeError('Lost response');
        if (status === 'error') throw new Error('Unclassified failure');
        return { ok: false, status };
      };
      try {
        const op = f.startFaceMutation(kind, [1], () => f[kind]([1], 8), 'person-faces', [8], {
          rollbackOnError: true, onError: () => { errors++; },
        });
        assert.deepEqual(e.projectList('person-faces', 'face', [{ id: 1 }]).items, []);
        assert.equal((await op.done).state, [403, 404, 422].includes(status) ? 'rejected' : 'unknown');
        assert.deepEqual(e.projectList('person-faces', 'face', [{ id: 1 }]).items, [{ id: 1 }]);
        await tick();
        assert.equal(errors, 1);
        assert.equal(refreshes, [403, 404, 422].includes(status) ? 0 : 1);
        assert.equal(e.hasOperationLookup(op.id), true);
      } finally { global.fetch = original; }
    }
  }
});

test('bulk unknown intent remains until durable lookup then releases resource locks', async () => {
  const e = environment(), { runtime, load } = require('./datasetRuntime.cjs');
  const f = load('services/faceActions.ts', runtime(), { '../stores/optimisticOperations': e });
  let finishRefresh, calls = 0;
  e.registerQueryRefresh('faces', () => new Promise(resolve => { finishRefresh = resolve; }));
  const op = f.startFaceMutation('Bulk detach', [1, 2], async () => { throw Error('Lost response'); }, 'faces');
  assert.equal((await op.done).state, 'unknown'); await tick();
  assert.deepEqual(e.projectList('faces', 'face', [{ id: 1 }, { id: 2 }]).items, []);
  const next = e.startPeopleMutation('Next', [], async () => { calls++; }, { entityResources: ['face:1'] });
  await tick(); assert.equal(calls, 0);
  finishRefresh(); await tick();
  assert.equal(calls, 0);
  await e.reconcileOperation(op.id, async () => ({operation_id: op.id, state: "failed", applied: [], skipped: [], failed: [{key:"face:1",code:"rejected",message:"no write"},{key:"face:2",code:"rejected",message:"no write"}], unknown: []}));
  await next.done;
  assert.equal(calls, 1);
  assert.equal(e.optimisticOperations.getSnapshot().find(x => x.id === op.id).state, 'rejected');
  assert.deepEqual(e.projectList('faces', 'face', [{ id: 1 }, { id: 2 }]).items, [{ id: 1 }, { id: 2 }]);
});
