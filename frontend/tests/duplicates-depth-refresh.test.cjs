const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

const root = path.resolve(__dirname, '../src');
function environment(overrides) {
  const cache = new Map();
  function load(relative) {
    const file = path.resolve(root, relative);
    if (cache.has(file)) return cache.get(file).exports;
    const module = { exports: {} }; cache.set(file, module);
    const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText;
    const req = id => {
      if (!id.startsWith('.')) return require(id);
      const target = path.relative(root, path.resolve(path.dirname(file), id));
      return target in overrides ? overrides[target] : load(target + '.ts');
    };
    new Function('require', 'module', 'exports', code)(req, module, module.exports);
    return module.exports;
  }
  return { load };
}

// Cursor-paginated fake of GET /api/duplicates (limit 1..50).
function server(t, count) {
  let groups = Array.from({ length: count }, (_, index) => ({ group_id: index + 1, items: [{ id: index * 10 + 1 }, { id: index * 10 + 2 }] }));
  const requests = [];
  const oldFetch = global.fetch;
  global.fetch = async url => {
    const params = new URL(url, 'http://x').searchParams;
    const limit = Number(params.get('limit'));
    assert.ok(limit >= 1 && limit <= 50, `limit ${limit} within the API cap`);
    const after = Number(params.get('cursor') ?? 0);
    const start = groups.findIndex(group => group.group_id > after);
    const page = start < 0 ? [] : groups.slice(start, start + limit);
    const last = page.at(-1);
    requests.push({ cursor: params.get('cursor'), limit });
    const more = last && groups.indexOf(last) < groups.length - 1;
    return { ok: true, json: async () => ({ items: page, next_cursor: more ? String(last.group_id) : null }) };
  };
  t.after(() => { global.fetch = oldFetch; });
  return { requests, remove: id => { groups = groups.filter(group => group.group_id !== id); } };
}

test('an authoritative refresh re-reads every loaded duplicate page, not just the first', async (t) => {
  const api = server(t, 120);
  const e = environment({ config: { API: '' } });
  const store = e.load('stores/useListStore.ts');
  const duplicates = e.load('services/duplicates.ts');
  const key = 'duplicate-groups-count--2-';
  const depth = () => store.useListStore.getState().lists[key]?.items.length ?? 0;
  const first = () => duplicates.getDuplicatesThrough(depth(), 'count', undefined, 10, 2, null);
  const next = cursor => duplicates.getDuplicates(cursor, 'count', undefined, 10, 2, null);

  await store.useListStore.getState().fetchInitial(key, first);
  assert.equal(depth(), 10, 'a fresh list still starts with one 10-group page');
  for (let page = 0; page < 6; page++) await store.useListStore.getState().loadMore(key, next);
  assert.equal(depth(), 70);

  api.remove(3); // resolved elsewhere; the server is the source of truth
  api.requests.length = 0;
  await store.refreshCachedList(key);
  const list = store.useListStore.getState().lists[key];
  assert.equal(list.items.length, 70, 'the refreshed list keeps the scrolled-in depth');
  assert.equal(list.items.some(group => group.group_id === 3), false);
  assert.deepEqual(api.requests.map(request => request.limit), [50, 20], 'API pages stay within the limit cap');
  assert.equal(list.hasMore, true);

  await store.useListStore.getState().loadMore(key, next);
  const ids = store.useListStore.getState().lists[key].items.map(group => group.group_id);
  assert.equal(ids.length, 80);
  assert.equal(new Set(ids).size, 80, 'paging continues from the refreshed cursor without repeats');
});

test('a short library stops at the last page instead of requesting past it', async (t) => {
  const api = server(t, 7);
  const duplicates = environment({ config: { API: '' } }).load('services/duplicates.ts');
  const page = await duplicates.getDuplicatesThrough(40, 'count', undefined, 10, 2, null);
  assert.equal(page.items.length, 7);
  assert.equal(page.next_cursor, null);
  assert.equal(api.requests.length, 1);
});

test('the page skips re-reads for events it already reconciles locally', () => {
  const { duplicateListNeedsRefresh } = environment({}).load('services/duplicateResolver.ts');
  assert.equal(duplicateListNeedsRefresh({ type: 'duplicates:resolved', groupId: 1, mediaIds: [2] }), false);
  assert.equal(duplicateListNeedsRefresh({ type: 'media:deleted', ids: [2] }), false);
  assert.equal(duplicateListNeedsRefresh({ type: 'media:updated', items: [{ id: 2 }] }), true);
  assert.equal(duplicateListNeedsRefresh({ type: 'list:invalidate', prefix: '' }), true);
});
