/* eslint-disable @typescript-eslint/no-require-imports -- Node harness transpiles the actual hooks and component. */
const test = require('node:test');
const assert = require('node:assert/strict');
const { runtime, load, nodes } = require('../../../tests/datasetRuntime.cjs');

function focusHarness() {
  const rt = runtime();
  rt.api.useLayoutEffect = rt.api.useEffect;
  const keys = new Map(), scrolls = [];
  const { useDuplicateGroupFocus } = load('hotkeys/useDuplicateGroupFocus.ts', rt, {
    './useHotkey': { useHotkey(binding, handler, options) { keys.set(binding.key, { handler, options }); } },
  });
  let ids = [10, 20, 30], key = 'queue';
  const scroll = index => scrolls.push(index);
  const render = () => rt.render(() => useDuplicateGroupFocus(ids, key, scroll));
  return { render, scrolls, key: value => { key = value; }, ids: value => { ids = value; }, press(value) {
    const entry = keys.get(value);
    if (entry.options.enabled !== false) entry.handler();
    return render();
  } };
}

test('j/k focus queued groups in order, clamp at edges, and Escape clears', () => {
  const h = focusHarness();
  assert.equal(h.render().focusedId, null);
  assert.equal(h.press('j').focusedId, 10);
  assert.equal(h.press('j').focusedId, 20);
  assert.equal(h.press('k').focusedId, 10);
  assert.equal(h.press('k').focusedId, 10);
  assert.equal(h.press('Escape').focusedId, null);
  assert.equal(h.press('j').focusedId, 10);
  assert.ok(h.scrolls.includes(1), 'virtual list scrolls to offscreen targets');
});

test('removal advances to the next surviving group and cursor arrivals continue the queue', () => {
  const h = focusHarness(); h.render(); h.press('j'); h.press('j');
  h.ids([10, 30]);
  assert.equal(h.render().focusedId, 30);
  h.ids([10]);
  assert.equal(h.render().focusedId, null, 'last row does not jump back to an earlier group');
  // A fresh cursor page is appended to earlier rows; focus the newly arrived row.
  h.ids([10, 40]);
  assert.equal(h.render().focusedId, 40);
  h.key('different-filter');
  assert.equal(h.render().focusedId, null);
});

test('page loads, prunes and refreshes do not pull the window back to a focused group', () => {
  const h = focusHarness(); h.render(); h.press('j');
  assert.deepEqual(h.scrolls, [0]);
  // The user scrolls away from group 10; cursor pages and removals elsewhere change ids.
  h.ids([10, 20, 30, 40, 50]); h.render();
  h.ids([10, 30, 40, 50]); h.render();
  h.ids([10, 30, 40, 50, 60, 70]); h.render();
  assert.deepEqual(h.scrolls, [0], 'only a focus move may scroll the window');
  // A real focus move still scrolls: removing the focused group advances to the next one.
  h.ids([30, 40, 50, 60, 70]);
  assert.equal(h.render().focusedId, 30);
  assert.deepEqual(h.scrolls, [0, 0]);
});

test('focused Enter opens confirmation only and snapshots group, selected master, and membership', async () => {
  const rt = runtime(), keys = [], calls = [];
  const { DuplicateGroup } = load('components/DuplicateGroup.tsx', rt, {
    './DuplicateMediaCard': { DuplicateMediaCard: 'DuplicateMediaCard' },
    '@mui/material/Grid': { __esModule: true, default: 'Grid' },
    '../hotkeys/useHotkey': { useHotkeys(bindings, handler, options) { keys.push({ bindings, handler, options }); } },
    '../services/duplicates': { resolveDuplicates: async (...args) => calls.push(args) },
  });
  const requested = [];
  let props = { group: { group_id: 10, items: [{ id: 101 }, { id: 102 }] }, focused: false,
    masterId: 102, selectedIds: new Set(), onSelectMaster() {}, onFocusGroup() {}, onResolveRequested(snapshot) { requested.push(snapshot); } };
  const render = () => rt.render(() => DuplicateGroup(props));
  const dialog = tree => nodes(tree, node => node.props.title === 'Confirm Action')[0];
  render();
  assert.equal(keys.at(-1).options.enabled, false, 'Enter is disabled without group focus');
  // Keep RAF pending: DOM focus is tested in browser, the callback is cancelled on cleanup.
  const oldRequest = global.requestAnimationFrame, oldCancel = global.cancelAnimationFrame;
  global.requestAnimationFrame = () => 1; global.cancelAnimationFrame = () => {};
  try {
    props = { ...props, focused: true }; const tree = render();
    const paper = nodes(tree, node => node.type === 'Paper')[0];
    assert.equal(paper.props.tabIndex, -1);
    assert.equal(paper.props['aria-label'], 'Group 10, 2 items');
    const enter = keys.at(-1);
    assert.equal(enter.options.scope, 'page');
    assert.equal(enter.options.allowInInput, undefined, 'shared typing guard remains enabled');
    assert.equal(enter.bindings.find(binding => binding.key === 'Enter').destructive, true);
    enter.handler({ key: 'Enter' });
    assert.deepEqual(calls, []);
    assert.equal(dialog(render()).props.open, true);
    props = { ...props, masterId: 101, group: { group_id: 99, items: [{ id: 201 }, { id: 202 }] }, onResolveRequested() { throw Error('wrong callback'); } };
    // Optimistic: confirm hands the snapshot taken at open to the page and closes at once.
    dialog(render()).props.onConfirm();
    assert.deepEqual(requested, [{ action: 'DELETE_FILES', groupId: 10, masterId: 102, memberIds: [101, 102] }]);
    assert.deepEqual(calls, [], 'the group never awaits the server');
    assert.equal(dialog(render()).props.open, false);
    rt.unmount();
  } finally { global.requestAnimationFrame = oldRequest; global.cancelAnimationFrame = oldCancel; }
});
