/* eslint-disable @typescript-eslint/no-require-imports -- CommonJS harness uses Node built-ins and the installed TypeScript compiler. */
const test = require('node:test');
const assert = require('node:assert/strict');
const { harness, find } = require('./viewer-regression.test.cjs');

const button = (tree, label) => find(tree, 'Button', (props) => [props.children].flat().includes(label));
const tick = () => new Promise((resolve) => setImmediate(resolve));
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((accept, fail) => { resolve = accept; reject = fail; });
  return { promise, resolve, reject };
};

function selectionBar(deleteMedia = async () => ({ removed: 1, processed_ids: [101], skipped_ids: [102], errors: [] }), presentationMode = false) {
  const calls = [];
  const removed = [];
  let clears = 0;
  const selection = {
    selectedIds: new Set([101, 102]), loadedCount: 2, hasMore: false, listKey: 'original-list',
    clear: () => { clears++; selection.selectedIds = new Set(); },
  };
  const instance = harness('frontend/src/components/SelectionActionBar.tsx', {
    'react-router-dom': { useLocation: () => ({ pathname: '/blur' }), matchPath: () => null },
    '../context/SelectionContext': { useSelection: () => selection },
    '../context/UndoContext': { useUndo: () => ({ push() {}, refreshVisible() {} }) },
    '../stores/useListStore': { useListStore: () => ({ removeItems: (...args) => removed.push(args) }) },
    '../stores/useLastEditStore': { useLastEditStore: (selector) => selector({ ops: null }) },
    '../services/mediaActions': { bulkDeleteMedia: async (ids, action) => { calls.push({ ids, action }); return deleteMedia(ids, action); } },
    '../config': { __esModule: true, default: { PRESENTATION_MODE: presentationMode } },
    './BulkResolveToolbar': { resolveConfirmCopy: (action, count) => ({ title: action, message: `Delete ${count}`, confirmLabel: 'Confirm' }) },
  });
  return { ...instance, selection, calls, removed, clears: () => clears };
}
const confirmation = (tree) => find(tree, 'ConfirmDialog', (props) => props.title !== 'Detach from this person?');

for (const [label, action] of [
  ['Delete files…', 'DELETE_FILES'],
  ['Remove records…', 'DELETE_RECORDS'],
  ['Blacklist…', 'BLACKLIST_RECORDS'],
]) {
  test(`${action} snapshots selection and list, and removes only processed ids`, async () => {
    const bar = selectionBar();
    button(bar.render(), label).props.onClick();
    assert.equal(bar.calls.length, 0, 'opening the dialog must never perform deletion');
    const opened = confirmation(bar.render());
    assert.equal(opened.props.open, true);
    assert.match(opened.props.message, /2/, 'confirmation retains the selection count');
    bar.selection.selectedIds.clear();
    bar.selection.selectedIds.add(202);
    bar.selection.listKey = 'later-list';
    await confirmation(bar.render()).props.onConfirm();
    assert.deepEqual(bar.calls, [{ ids: [101, 102], action }]);
    assert.deepEqual(bar.removed, [['original-list', [101]]], 'skipped media must remain cached');
    assert.equal(bar.clears(), 1);
    const finished = bar.render();
    assert.equal(confirmation(finished).props.open, false);
    assert.equal(find(finished, 'Alert').props.children, 'Deleted 1; 1 skipped');
  });
}

test('bulk deletion reports partial failures while retaining unprocessed ids in the list', async () => {
  const bar = selectionBar(async () => ({ removed: 1, processed_ids: [101], skipped_ids: [102], errors: [{ media_id: 103, error: 'Permission denied' }] }));
  bar.selection.selectedIds.add(103);
  button(bar.render(), 'Delete files…').props.onClick();
  await confirmation(bar.render()).props.onConfirm();
  const alert = find(bar.render(), 'Alert');
  assert.equal(alert.props.severity, 'error');
  assert.equal(alert.props.children, 'Deleted 1; 1 skipped; 1 failed');
  assert.deepEqual(bar.removed, [['original-list', [101]]]);
});

test('failed bulk-delete request preserves selection and confirmation for retry', async () => {
  const bar = selectionBar(async () => { throw new Error('Delete unavailable'); });
  button(bar.render(), 'Remove records…').props.onClick();
  await confirmation(bar.render()).props.onConfirm();
  const failed = bar.render();
  assert.equal(bar.clears(), 0);
  assert.deepEqual(bar.removed, []);
  assert.equal(confirmation(failed).props.open, true);
  assert.equal(confirmation(failed).props.loading, false);
  assert.equal(find(failed, 'Alert').props.children, 'Delete unavailable');
  assert.equal(find(failed, 'Alert').props.severity, 'error');
});

test('presentation mode prevents bulk-delete dialogs and submissions', () => {
  const bar = selectionBar(undefined, true);
  const tree = bar.render();
  for (const label of ['Delete files…', 'Remove records…', 'Blacklist…']) {
    assert.equal(button(tree, label).props.disabled, true);
    button(tree, label).props.onClick();
  }
  assert.equal(confirmation(bar.render()).props.open, false);
  assert.deepEqual(bar.calls, []);
});

test('ConfirmDialog autofocuses Confirm and does not accept Enter or clicks while loading', async () => {
  let calls = 0;
  const dialog = harness('frontend/src/components/ConfirmDialog.tsx');
  const props = { open: true, title: 'Delete', message: 'Delete?', loading: true, onConfirm: () => { calls++; }, onClose() {} };
  const tree = dialog.render(props);
  assert.equal(button(tree, 'Confirm').props.autoFocus, true);
  assert.equal(button(tree, 'Confirm').props.disabled, true);
  assert.equal(button(tree, 'Cancel').props.disabled, true);
  assert.equal(find(tree, 'Dialog').props.onClose, undefined, 'Escape/backdrop cannot close while busy');
  dialog.hotkeys.find((entry) => entry.binding.key === 'Enter').handler({ key: 'Enter' });
  button(tree, 'Confirm').props.onClick();
  await tick();
  assert.equal(calls, 0);
});

test('ConfirmDialog Enter and clicks share an async single-flight guard', async () => {
  const pending = deferred();
  let calls = 0;
  const dialog = harness('frontend/src/components/ConfirmDialog.tsx');
  const tree = dialog.render({ open: true, title: 'Delete', message: 'Delete?', onConfirm: () => { calls++; return pending.promise; }, onClose() {} });
  const enter = dialog.hotkeys.find((entry) => entry.binding.key === 'Enter');
  assert.equal(enter.options.destructive, true, 'the provider must suppress repeated destructive Enter events');
  enter.handler({ key: 'Enter' });
  button(tree, 'Confirm').props.onClick();
  enter.handler({ key: 'Enter' });
  assert.equal(calls, 1);
  pending.resolve();
  await tick();
  button(tree, 'Confirm').props.onClick();
  assert.equal(calls, 2);
});
