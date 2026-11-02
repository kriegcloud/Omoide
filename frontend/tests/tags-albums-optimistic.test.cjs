const { test } = require('node:test');
const assert = require('node:assert/strict');
const { runtime, load, nodes, deferred, flush } = require('./datasetRuntime.cjs');

function controller() {
  const records = [];
  return { records, optimisticOperations: { subscribe: () => () => {}, getSnapshot: () => [] }, projectEntity: (_, base) => base, operationVersion: () => 0, acknowledgeQuery() {}, startOperation(command) {
    const entry = { command, state: 'pending' }; records.push(entry);
    const done = Promise.resolve().then(() => command.request(command.input, 'fixture')).then(result => {
      entry.state = 'committed'; return entry;
    }, error => { entry.state = 'unknown'; entry.error = error.message; return entry; });
    return { id: 'fixture', done };
  }};
}

test('album add dismisses immediately, never enumerates membership, and undoes only receipt IDs', async () => {
  const rt = runtime(), pending = deferred(), ops = controller(); let closed = 0, preflights = 0, undo, removed;
  const album = { id: 1, name: 'Fixture', media_count: 2 };
  const { AddToAlbumDialog } = load('components/AddToAlbumDialog.tsx', rt, {
    '../stores/optimisticOperations': ops,
    '../services/albums': { getAlbumMemberIds: async () => { preflights++; return new Set(); } },
    '../context/UndoContext': { useUndo: () => ({ push: value => { undo = value; }, refreshVisible: async () => {} }) },
    '../services/features': { getAlbums: async () => [album], addMediaToAlbum: () => pending.promise, removeMediaFromAlbum: async (_, ids) => { removed = ids; } },
  });
  const props = { open: true, mediaIds: [1, 2, 999], onClose: () => closed++, onAdded() {} };
  rt.render(() => AddToAlbumDialog(props)); await flush();
  const tree = rt.render(() => AddToAlbumDialog(props));
  nodes(tree, n => n.type === 'ListItemButton')[0].props.onClick();
  assert.equal(closed, 1); assert.equal(preflights, 0);
  await flush(); pending.resolve({ ...album, added_ids: [2], removed_ids: [], skipped_ids: [1, 999] }); await flush();
  await undo.undo(); assert.deepEqual(removed, [2]);
});

test('tag removal hides immediately and failed request is globally visible after unmount', async () => {
  const rt = runtime(), ops = controller(), pending = deferred();
  const owner = { id: 1, tags: [{ id: 3, name: 'fixture' }] };
  const { Tags } = load('components/Tags.tsx', rt, {
    '../stores/optimisticOperations': ops,
    '../services/tagActions': { removeTagFromMedia: () => pending.promise },
  });
  const render = () => rt.render(() => Tags({ media: owner, onUpdate() {} }));
  nodes(render(), n => n.type === 'Chip')[0].props.onDelete({ preventDefault() {}, stopPropagation() {} });
  assert.equal(nodes(render(), n => n.type === 'Chip').length, 0);
  rt.unmount(); await flush(); pending.reject(new Error('denied')); await flush();
  assert.match(ops.records[0].error, /denied/);
});

test('new tag has provisional pending chip, duplicate submission is deduplicated, real ID replaces it', async () => {
  const rt = runtime(), ops = controller(), created = deferred(), assigned = deferred(); let creates = 0, added;
  global.window = { setTimeout: () => 0, clearTimeout() {} };
  const { default: TagAdder } = load('components/TagAdder.tsx', rt, {
    '../stores/optimisticOperations': ops,
    '../services/tagging': { createTag: () => { creates++; return created.promise; }, assignTag: () => assigned.promise },
  });
  const render = () => rt.render(() => TagAdder({ ownerType: 'media', ownerId: 1, existingTags: [], onTagAdded: tag => { added = tag; } }));
  const choose = () => nodes(render(), n => n.type === 'Autocomplete')[0].props.onChange({}, 'Fixture');
  choose(); choose(); await flush();
  assert.equal(creates, 1);
  assert.equal(nodes(render(), n => n.type === 'Chip').length, 1);
  created.resolve({ id: 8, name: 'fixture' }); await flush();
  assert.equal(added, undefined);
  assigned.resolve(); await flush(); assert.equal(added.id, 8);
  assert.equal(nodes(render(), n => n.type === 'Chip').length, 0);
});

test('album member removal immediately overlays selected rows; Undo adds only removed IDs', async () => {
  const rt = runtime(), ops = controller(), pending = deferred(); let undo, added;
  const album = { id: 1, name: 'Fixture', media_count: 3 };
  const { default: Page } = load('pages/AlbumDetailPage.tsx', rt, {
    '../stores/optimisticOperations': ops,
    '../context/SelectionContext': { useSelection: () => ({ selectedIds: new Set([2, 999]), clear() {}, isSelecting: true }) },
    '../context/UndoContext': { useUndo: () => ({ push: value => { undo = value; }, refreshVisible: async () => {} }) },
    '../services/features': { getAlbum: async () => album, removeMediaFromAlbum: () => pending.promise, addMediaToAlbum: async (_, ids) => { added = ids; } },
  });
  rt.render(Page); await flush();
  const tree = rt.render(Page);
  const remove = nodes(tree, n => n.type === 'Button' && String(n.props.children).includes('Remove'))[0];
  remove.props.onClick();
  assert.deepEqual(ops.records[0].command.patches.map(p => [p.kind, p.query, p.key]), [['remove', 'album-1', 'media:2'], ['remove', 'album-1', 'media:999']]);
  await flush(); pending.resolve({ ...album, removed_ids: [2], added_ids: [], skipped_ids: [999] }); await flush();
  await undo.undo(); assert.deepEqual(added, [2]);
});

for (const kind of ['album', 'tag']) test(`${kind} bulk delete snapshots dialog targets and closes before response`, async () => {
  const rt = runtime(), ops = controller(), pending = deferred(), selected = new Set([1]); let submitted;
  const selector = { selectedIds: selected, selectionMode: true, pruneTo() {}, enterMode() {}, exitMode() {}, setSelected() {}, toggleMode() {} };
  const { default: Page } = load(`pages/${kind === 'album' ? 'AlbumsPage' : 'TagPage'}.tsx`, rt, {
    '../stores/optimisticOperations': { ...ops, optimisticOperations: { subscribe: () => () => {}, getSnapshot: () => [] }, projectList: (_, __, items) => ({ items }), operationVersion: () => 0, acknowledgeQuery() {} },
    '../hooks/useEntitySelection': { useEntitySelection: () => selector },
    '../stores/useListStore': { useListStore: fn => fn({ lists: { 'tags-all': { items: [{ id: 1, name: 'x' }], hasMore: false } }, fetchInitial() {}, removeItem() {}, removeItems() {} }), defaultListState: {} },
    '../services/features': { getAlbums: async () => [{ id: 1, name: 'x' }] },
    [`../services/${kind === 'album' ? 'albums' : 'tag'}`]: { [kind === 'album' ? 'deleteAlbumsBulk' : 'deleteTagsBulk']: ids => { submitted = ids; return pending.promise; } },
  });
  // Runtime's useSyncExternalStore uses React internals; keep its snapshot hook local.
  rt.api.useSyncExternalStore = (_, get) => get();
  rt.render(Page); await flush();
  nodes(rt.render(Page), n => n.type === 'Button' && n.props.children === 'Delete Selected')[0].props.onClick();
  selected.clear(); selected.add(9);
  const dialog = nodes(rt.render(Page), n => String(n.type).includes('ConfirmDialog'))[0];
  dialog.props.onConfirm(); await flush();
  assert.deepEqual(submitted, [1]);
  assert.equal(nodes(rt.render(Page), n => String(n.type).includes('ConfirmDialog'))[0].props.open, false);
  pending.resolve({ deleted_ids: [1], skipped_ids: [] }); await flush(); rt.unmount();
});

test('album rename closes immediately and projects the draft through the shared controller', async () => {
  const rt = runtime(), ops = controller(), pending = deferred();
  const { default: Page } = load('pages/AlbumDetailPage.tsx', rt, {
    '../stores/optimisticOperations': { ...ops, optimisticOperations: { subscribe: () => () => {}, getSnapshot: () => [] }, projectEntity: (_, base) => base, operationVersion: () => 0, acknowledgeQuery() {} },
    '../services/features': { getAlbum: async () => ({ id: 1, name: 'Old', media_count: 0 }), updateAlbum: () => pending.promise },
  });
  rt.api.useSyncExternalStore = (_, get) => get();
  rt.render(Page); await flush();
  nodes(rt.render(Page), n => n.type === 'TextField')[0].props.onChange({ target: { value: 'New' } });
  nodes(rt.render(Page), n => n.type === 'Button' && n.props.children === 'Rename')[0].props.onClick();
  assert.deepEqual(ops.records[0].command.patches[0].values, { name: 'New' });
  assert.equal(nodes(rt.render(Page), n => n.type === 'Dialog')[0].props.open, false);
  pending.resolve({ id: 1, name: 'New', media_count: 0 }); await flush();
});

for (const [file, fn, args] of [['tagging', 'assignTag', ['media', 1, 2]], ['tagActions', 'removeTagFromMedia', [1, 2]], ['features', 'removeMediaFromAlbum', [1, [2]]]]) test(`${file} validation rejection is definitive, transport loss stays uncertain`, async () => {
  const rt = runtime();
  class DefinitiveMutationError extends Error {}
  let failure = Object.assign(new Error('forbidden'), { status: 403 });
  const service = load(`services/${file}.ts`, rt, {
    './api': { apiFetch: async () => { throw failure; } },
    '../stores/optimisticOperations': { DefinitiveMutationError },
  });
  await assert.rejects(service[fn](...args), DefinitiveMutationError);
  failure = new TypeError('lost response');
  await assert.rejects(service[fn](...args), error => error === failure);
});

test('album creation closes dialog and publishes a provisional card before receiving its real ID', async () => {
  const rt = runtime(), ops = controller(), pending = deferred();
  let base;
  const { default: Page } = load('pages/AlbumsPage.tsx', rt, {
    '../stores/optimisticOperations': { ...ops, projectList: (_, __, items) => { base = items; return { items }; } },
    '../hooks/useEntitySelection': { useEntitySelection: () => ({ selectedIds: new Set(), pruneTo() {} }) },
    '../services/features': { getAlbums: async () => [], createAlbum: () => pending.promise },
  });
  rt.render(Page); await flush();
  nodes(rt.render(Page), n => n.type === 'Button' && n.props.children === 'New Album')[0].props.onClick();
  nodes(rt.render(Page), n => n.type === 'TextField')[0].props.onChange({ target: { value: 'New album' } });
  nodes(rt.render(Page), n => n.type === 'Button' && n.props.children === 'Create')[0].props.onClick();
  const tree = rt.render(Page);
  assert.equal(nodes(tree, n => n.type === 'Dialog')[0].props.open, false);
  assert.equal(base.length, 1); assert.ok(base[0].id < 0);
  pending.resolve({ id: 7, name: 'New album', media_count: 0 }); await flush(); rt.render(Page);
  assert.deepEqual(base.map(row => row.id), [7]);
});

test('album detail deletion navigates immediately and hides the snapshotted album', async () => {
  const rt = runtime(), ops = controller(), pending = deferred(), navigation = [];
  const { default: Page } = load('pages/AlbumDetailPage.tsx', rt, {
    '../stores/optimisticOperations': ops,
    'react-router-dom': { useParams: () => ({ id: '1' }), useNavigate: () => route => navigation.push(route) },
    '../services/features': { getAlbum: async () => ({ id: 1, name: 'Fixture', media_count: 0 }), deleteAlbum: () => pending.promise },
  });
  rt.render(Page); await flush();
  nodes(rt.render(Page), n => n.type === 'IconButton' && n.props.color === 'error')[0].props.onClick();
  nodes(rt.render(Page), n => String(n.type).includes('ConfirmDialog'))[0].props.onConfirm();
  assert.deepEqual(navigation, ['/albums']);
  assert.equal(ops.records[0].command.patches[0].key, 'album:1');
  pending.resolve(); await flush();
});

test('tag removal finishing on another owner type cannot update that owner with the same numeric ID', async () => {
  const rt = runtime(), ops = controller(), pending = deferred(); let updates = 0;
  const { Tags } = load('components/Tags.tsx', rt, {
    '../stores/optimisticOperations': ops, '../services/tagActions': { removeTagFromMedia: () => pending.promise },
  });
  let props = { media: { id: 1, tags: [{ id: 3, name: 'x' }] }, onUpdate: () => updates++ };
  nodes(rt.render(() => Tags(props)), n => n.type === 'Chip')[0].props.onDelete({ preventDefault() {}, stopPropagation() {} });
  props = { person: { id: 1, tags: [{ id: 3, name: 'x' }] }, onUpdate: () => updates++ };
  rt.render(() => Tags(props)); await flush(); pending.resolve(); await flush();
  assert.equal(updates, 0);
});

function coreEnvironment() {
  const fs = require('node:fs'), path = require('node:path'), ts = require('typescript'), cache = new Map();
  function read(relative) {
    const file = path.resolve(__dirname, '../src', relative);
    if (cache.has(file)) return cache.get(file).exports;
    const module = { exports: {} }; cache.set(file, module);
    const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
    new Function('require', 'module', 'exports', code)(id => id.startsWith('.') ? read(path.relative(path.resolve(__dirname, '../src'), path.resolve(path.dirname(file), id + '.ts'))) : require(id), module, module.exports);
    return module.exports;
  }
  return read('stores/optimisticOperations.ts');
}

test('real controller restores only skipped memberships and never removes media from other galleries', async () => {
  const rt = runtime(), core = coreEnvironment(), pending = deferred(); let undo;
  const { default: Page } = load('pages/AlbumDetailPage.tsx', rt, {
    '../stores/optimisticOperations': core,
    '../context/SelectionContext': { useSelection: () => ({ selectedIds: new Set([2, 3]), clear() {}, isSelecting: true }) },
    '../context/UndoContext': { useUndo: () => ({ push: value => { undo = value; }, refreshVisible: async () => {} }) },
    '../services/features': { getAlbum: async () => ({ id: 1, name: 'x', media_count: 2 }), removeMediaFromAlbum: () => pending.promise },
  });
  rt.render(Page); await flush();
  nodes(rt.render(Page), n => n.type === 'Button' && String(n.props.children).includes('Remove'))[0].props.onClick();
  const base = [{ id: 2 }, { id: 3 }];
  assert.deepEqual(core.projectList('album-1', 'media', base).items, []);
  assert.deepEqual(core.projectList('images', 'media', base).items, base);
  await flush(); pending.resolve({ id: 1, name: 'x', media_count: 1, removed_ids: [2], skipped_ids: [3], added_ids: [] }); await flush();
  assert.deepEqual(core.projectList('album-1', 'media', base).items, [{ id: 3 }]);
  const record = core.optimisticOperations.getSnapshot()[0];
  assert.equal(record.state, 'partially_committed');
  assert.equal(undo.operationId, record.id);
});

test('real controller rolls album rows back for definitive rejection while keeping global error', async () => {
  const rt = runtime(), core = coreEnvironment(), pending = deferred();
  const { default: Page } = load('pages/AlbumDetailPage.tsx', rt, {
    '../stores/optimisticOperations': core,
    '../context/SelectionContext': { useSelection: () => ({ selectedIds: new Set([2]), clear() {}, isSelecting: true }) },
    '../services/features': { getAlbum: async () => ({ id: 1, name: 'x', media_count: 1 }), removeMediaFromAlbum: () => pending.promise },
  });
  rt.render(Page); await flush();
  nodes(rt.render(Page), n => n.type === 'Button' && String(n.props.children).includes('Remove'))[0].props.onClick();
  rt.unmount(); await flush(); pending.reject(new core.DefinitiveMutationError('forbidden')); await flush();
  assert.deepEqual(core.projectList('album-1', 'media', [{ id: 2 }]).items, [{ id: 2 }]);
  assert.equal(core.optimisticOperations.getSnapshot()[0].error, 'forbidden');
});

test('tag detail keeps the current tag and gallery mounted during background metadata refresh', async () => {
  const rt = runtime(), pending = deferred(); let refresh, reads = 0;
  const tag = { id: 1, name: 'x', persons: [] };
  const { default: Page } = load('pages/TagDetailPage.tsx', rt, {
    '../context/UndoContext': { useMutationRefresh: (_, callback) => { refresh = callback; } },
    '../services/tag': { getTag: () => ++reads === 1 ? Promise.resolve(tag) : pending.promise },
    '../stores/useListStore': { useListStore: fn => fn({ lists: {}, fetchInitial() {}, loadMore() {}, clearList() {} }), defaultListState: { items: [], hasMore: false } },
  });
  rt.render(Page); await flush(); refresh(); const tree = rt.render(Page);
  assert.equal(tree.props.title, 'Tag: #x');
  pending.resolve(tag); await flush(); rt.unmount();
});
