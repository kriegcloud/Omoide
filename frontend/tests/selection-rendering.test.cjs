const assert = require('node:assert/strict');
const { test } = require('node:test');
const { runtime, load, nodes } = require('./datasetRuntime.cjs');

test('MediaCard is memoized and subscribes to only its own selection', () => {
  const rt = runtime(); let memoized = false; const ids = [];
  rt.api.memo = (component, compare) => { memoized = true; component.compare = compare; return component; };
  const MediaCard = load('components/MediaCard.tsx', rt, {
    'react-router-dom': { useLocation: () => ({ pathname: '/images', state: null }) },
    '../urlUtils': { encodeFilePath: encodeURIComponent },
    '../context/SelectionContext': {
      useSelection: () => ({ isSelecting: true, selectedIds: new Set([2]), toggle() {}, beginSelecting() {} }),
      useSelectionItem: (id) => { ids.push(id); return { isSelecting: true, isSelected: true, toggle() {}, beginSelecting() {} }; },
    },
  }).default;
  const tree = rt.render(() => MediaCard({ media: { id: 2, filename: 'test.jpg', path: 'test.jpg', width: 400, height: 300 } }));
  assert.equal(memoized, true);
  assert.deepEqual(ids, [2]);
  assert.equal(tree.props.selected, true);
  const thumb = nodes(tree, (node) => node.type === 'CardMedia')[0];
  assert.equal(thumb.props.loading, 'lazy');
  assert.equal(thumb.props.decoding, 'async');
  assert.equal(thumb.props.width, 400);
  assert.equal(thumb.props.height, 300);
  rt.unmount();
});

test('unrelated selected ids retain a tile snapshot while its own changes publish', () => {
  const { createSelectionSnapshotStore } = load('stores/selectionSnapshot.ts', runtime());
  const state = { isSelecting: true, selectedIds: new Set([1]), toggle() {}, beginSelecting() {} };
  const store = createSelectionSnapshotStore(state);
  let first = store.getItemSnapshot(1); let second = store.getItemSnapshot(2);
  let firstRenders = 0; let secondRenders = 0;
  const stopFirst = store.subscribe(() => { const next = store.getItemSnapshot(1); if (!Object.is(first, next)) { first = next; firstRenders++; } });
  const stopSecond = store.subscribe(() => { const next = store.getItemSnapshot(2); if (!Object.is(second, next)) { second = next; secondRenders++; } });
  store.update({ ...state, selectedIds: new Set([1, 2]) });
  assert.equal(firstRenders, 0);
  assert.equal(secondRenders, 1);
  store.update({ ...state, selectedIds: new Set([1, 2]), isSelecting: false });
  assert.equal(firstRenders, 1);
  assert.equal(secondRenders, 2);
  stopFirst(); stopSecond();
  store.update(state);
  assert.equal(firstRenders, 1);
});

test('tile actions remain stable and delegate to the latest selection provider', () => {
  const { createSelectionSnapshotStore } = load('stores/selectionSnapshot.ts', runtime());
  let called = '';
  const state = { isSelecting: false, selectedIds: new Set(), toggle: () => { called = 'old'; }, beginSelecting() {} };
  const store = createSelectionSnapshotStore(state);
  const toggle = store.toggle;
  store.update({ ...state, toggle: id => { called = `new:${id}`; } });
  assert.equal(toggle, store.toggle);
  toggle(8);
  assert.equal(called, 'new:8');
});

test('MediaCard memo compares navigation ids rather than fresh wrapper objects', () => {
  const rt = runtime();
  rt.api.memo = (component, compare) => { component.compare = compare; return component; };
  const MediaCard = load('components/MediaCard.tsx', rt).default;
  const media = { id: 1 }; const ids = [1, 2];
  assert.equal(MediaCard.compare({ media, navigationContext: { ids } }, { media, navigationContext: { ids } }), true);
  assert.equal(MediaCard.compare({ media }, { media: { ...media, is_favorite: true } }), false);
});
