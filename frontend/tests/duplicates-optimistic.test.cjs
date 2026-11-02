/* eslint-disable @typescript-eslint/no-require-imports -- Node harness transpiles the actual module. */
const test = require('node:test');
const assert = require('node:assert/strict');
const { runtime, load } = require('./datasetRuntime.cjs');

function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }

const snapshot = { action: 'DELETE_FILES', groupId: 7, masterId: 71, memberIds: [71, 72] };

function resolver(overrides = {}) {
  const rt = runtime();
  const { createDuplicateResolver } = load('services/duplicateResolver.ts', rt, {});
  const log = [];
  const pending = deferred();
  const r = createDuplicateResolver({
    resolve: (...args) => { log.push(['resolve', ...args]); return pending.promise; },
    removeGroup: s => log.push(['remove', s.groupId]),
    onSettled: s => log.push(['settled', s.groupId]),
    onError: (s, e) => log.push(['error', s.groupId, e.message]),
    refresh: () => log.push(['refresh']),
    onPendingChange: n => log.push(['pending', n]),
    ...overrides,
  });
  return { r, log, pending };
}

test('confirm removes the group immediately, before the server answers', () => {
  const { r, log } = resolver();
  r.request(snapshot);
  assert.deepEqual(log.slice(0, 3), [['remove', 7], ['pending', 1], ['resolve', 7, 'DELETE_FILES', 71, [71, 72]]]);
  assert.equal(r.pendingCount(), 1);
});

test('success settles and clears the pending count', async () => {
  const { r, log, pending } = resolver();
  r.request(snapshot);
  pending.resolve({ ok: true });
  await new Promise(setImmediate);
  assert.deepEqual(log.slice(3), [['settled', 7], ['pending', 0]]);
});

test('failure reports the group and re-reads the server state instead of guessing', async () => {
  const { r, log, pending } = resolver();
  r.request(snapshot);
  pending.reject(new Error('disk is read-only'));
  await new Promise(setImmediate);
  assert.deepEqual(log.slice(3), [['error', 7, 'disk is read-only'], ['refresh'], ['pending', 0]]);
});

test('a second confirm on the same group while pending is ignored', () => {
  const { r, log } = resolver();
  r.request(snapshot);
  r.request(snapshot);
  assert.equal(log.filter(entry => entry[0] === 'resolve').length, 1);
});

test('MARK_NOT_DUPLICATE sends no master', () => {
  const { r, log } = resolver();
  r.request({ ...snapshot, action: 'MARK_NOT_DUPLICATE' });
  assert.deepEqual(log.find(entry => entry[0] === 'resolve'), ['resolve', 7, 'MARK_NOT_DUPLICATE', undefined, [71, 72]]);
});

test('media deleted elsewhere (selection bar) prunes groups; groups under two items disappear', () => {
  const rt = runtime();
  const { pruneDuplicateGroups } = load('services/duplicateResolver.ts', rt, {});
  const groups = [
    { group_id: 1, items: [{ id: 11 }, { id: 12 }] },
    { group_id: 2, items: [{ id: 21 }, { id: 22 }, { id: 23 }] },
    { group_id: 3, items: [{ id: 31 }, { id: 32 }] },
  ];
  const { groups: next, removedGroupIds, changed } = pruneDuplicateGroups(groups, [11, 12, 21, 31]);
  assert.equal(changed, true);
  assert.deepEqual(removedGroupIds, [1, 3]);
  assert.deepEqual(next, [{ group_id: 2, items: [{ id: 22 }, { id: 23 }] }]);
  assert.equal(pruneDuplicateGroups(groups, [999]).changed, false);
  assert.equal(pruneDuplicateGroups(groups, [999]).groups, groups, 'no-op keeps identity');
});
